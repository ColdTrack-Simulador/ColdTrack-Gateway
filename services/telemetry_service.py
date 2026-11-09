from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from re import fullmatch
from typing import Any

from database import (
    get_latest_by_device,
    list_devices,
    list_telemetry,
    save_telemetry,
)

REQUIRED_FIELDS = [
    "deviceId",
    "estado",
    "diagnostico",
]

NORMAL_STATES = {"normal", "sin_fallas", "ok", "estable", "healthy", "refrigerando"}
ALERT_STATES = {
    "advert",
    "critico",
    "alerta",
    "alert",
    "falla",
    "fallo",
    "fault",
    "presion_alta",
    "presion_alta_critica",
    "presion_baja",
    "presion_baja_critica",
    "temp_alta",
    "temperatura_elevada",
    "temp_baja",
    "sobrecalentamiento",
    "compresor_fallado",
    "compresor fallado",
    "falla_ventilador",
    "problema_condensacion",
    "restriccion",
    "restriccion_circuito",
    "baja_carga_refrigerante",
    "baja_carga",
}

NORMAL_INTERVAL_SECONDS = 300
ALERT_INTERVAL_SECONDS = 5


def _normalize_text(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_")


def evaluate_device_state(payload: dict[str, Any]) -> str:
    estado = _normalize_text(payload.get("estado", ""))
    diagnostico = _normalize_text(payload.get("diagnostico", ""))
    if any(payload.get(key) in (True, 1) for key in ("proteccionAlta", "proteccionBaja", "ordenParoCompresor")):
        return "alert"

    if estado in {"normal"} and diagnostico in {"sin_fallas"}:
        return "normal"

    if estado in {"advert", "critico"}:
        return "alert"

    if diagnostico and diagnostico not in NORMAL_STATES:
        return "alert"

    if estado and estado not in NORMAL_STATES and estado not in {"advert", "critico"}:
        return "alert"

    return "normal"


def _parse_received_at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def should_store_telemetry(
    payload: dict[str, Any],
    last_reading: dict[str, Any] | None,
    now: str | None = None,
) -> bool:
    if last_reading is None:
        return True

    if now is None:
        now_value = datetime.now(timezone.utc).isoformat()
    else:
        now_value = now

    current_state = evaluate_device_state(payload)
    previous_state = evaluate_device_state(last_reading)

    if "diagnostico" in last_reading and (
        previous_state != current_state or payload.get("diagnostico") != last_reading["diagnostico"]
    ):
        return True

    last_received_at = _parse_received_at(last_reading.get("receivedAt") or last_reading.get("received_at"))
    current_received_at = _parse_received_at(now_value)

    if last_received_at is None or current_received_at is None:
        return True

    elapsed_seconds = (current_received_at - last_received_at).total_seconds()
    interval = ALERT_INTERVAL_SECONDS if current_state == "alert" else NORMAL_INTERVAL_SECONDS

    return elapsed_seconds >= interval


def validate_telemetry_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON.")

    faltantes = [campo for campo in REQUIRED_FIELDS if campo not in payload]
    if faltantes:
        raise ValueError("Faltan campos obligatorios.", faltantes)

    for campo in REQUIRED_FIELDS:
        valor = payload.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError(f"El campo '{campo}' debe ser un texto válido.")

    device_id = payload["deviceId"].strip()
    if not fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", device_id):
        raise ValueError("El identificador del equipo contiene caracteres inválidos.")

    for field in (
        "uptimeMs", "tempInterior", "tempCondensador", "tempSuccion", "presionAlta",
        "presionBaja", "compresor", "fanSolicitado", "fanPWM", "fanRpm",
    ):
        value = payload.get(field)
        if value is not None and (type(value) not in (int, float) or not -1e12 <= value <= 1e12):
            raise ValueError(f"El campo '{field}' debe ser un número válido.")

    return {
        **payload,
        "deviceId": device_id,
        "estado": payload["estado"].strip(),
        "diagnostico": payload["diagnostico"].strip(),
        "receivedAt": datetime.now(timezone.utc).isoformat(),
    }


def create_telemetry(payload: Any) -> dict[str, Any]:
    record = validate_telemetry_payload(payload)
    if record["deviceId"] == "SIM-001":
        raise ValueError("SIM-001 está reservado para el simulador.")
    for key in ("proteccionAlta", "proteccionBaja", "ordenParoCompresor"):
        if key in record:
            if type(record[key]) not in (bool, int) or record[key] not in (0, 1):
                raise ValueError(f"El campo '{key}' debe ser booleano.")
            record[key] = bool(record[key])
    record["reportedDiagnosis"] = record["diagnostico"]
    record["source"] = "gateway"
    if evaluate_device_state(record) == "alert" and _normalize_text(record["diagnostico"]) in NORMAL_STATES:
        record["diagnostico"] = "PROTECCION_ACTIVA" if any(
            record.get(key) for key in ("proteccionAlta", "proteccionBaja", "ordenParoCompresor")
        ) else record["estado"].strip().upper()
    return _record_telemetry(record)


def _record_telemetry(record: dict[str, Any]) -> dict[str, Any]:
    record["state"] = evaluate_device_state(record)
    record["severity"] = "critical" if record["state"] == "alert" else "normal"
    return save_telemetry(record, should_store_telemetry)


def simulate_telemetry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON.")
    readings = {}
    ranges = {
        "tempInterior": (-60, 80), "tempCondensador": (-60, 120), "tempSuccion": (-80, 80),
        "presionAlta": (0, 100), "presionBaja": (0, 100),
    }
    for field, (minimum, maximum) in ranges.items():
        value = payload.get(field)
        if type(value) not in (int, float) or not minimum <= value <= maximum or not isfinite(value):
            raise ValueError(f"{field} debe estar entre {minimum} y {maximum}.")
        readings[field] = value

    high_pressure = readings["presionAlta"] >= 90
    low_pressure = readings["presionBaja"] <= 10
    diagnosis = "SIN_FALLAS"
    if high_pressure:
        diagnosis = "FALLA_PRESION_ALTA"
    elif low_pressure:
        diagnosis = "FALLA_PRESION_BAJA"
    elif readings["tempInterior"] >= 5:
        diagnosis = "TEMPERATURA_ELEVADA"

    stopped = high_pressure or low_pressure
    record = {
        **readings,
        "deviceId": "SIM-001",
        "source": "simulator",
        "estado": "NORMAL" if diagnosis == "SIN_FALLAS" else "CRITICO",
        "diagnostico": diagnosis,
        "proteccionAlta": high_pressure,
        "proteccionBaja": low_pressure,
        "ordenParoCompresor": stopped,
        "compresor": 0 if stopped else 85,
        "fanSolicitado": 90,
        "fanPWM": 230,
        "fanRpm": 1450,
    }
    return _record_telemetry(validate_telemetry_payload(record))


def fetch_telemetry(device_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return [_with_state(record) for record in list_telemetry(device_id=device_id, limit=limit)]


def fetch_devices() -> list[dict[str, Any]]:
    return [_with_state(device) for device in list_devices()]


def fetch_latest_telemetry(device_id: str) -> dict[str, Any] | None:
    record = get_latest_by_device(device_id)
    return _with_state(record) if record else None


def _with_state(record: dict[str, Any]) -> dict[str, Any]:
    if not record.get("state") and record.get("estado"):
        return {**record, "state": evaluate_device_state(record)}
    return record
