from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from coldtrack.application import UseCaseError

telemetry_bp = Blueprint("telemetry_bp", __name__)


def telemetry():
    return current_app.extensions["coldtrack_telemetry"]


def _error_response(message: str, status_code: int, **extra):
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status_code


@telemetry_bp.get("/health")
def health():
    return jsonify(
        {
            "service": "ColdTrack API",
            "status": "ok",
            "version": "1.3.0",
            "storage": "sqlite",
            "architecture": "layered",
        }
    ), 200


@telemetry_bp.post("/api/telemetry")
def recibir_telemetria():
    if not request.is_json:
        return _error_response("El cuerpo debe ser JSON.", 415)

    try:
        payload = request.get_json(silent=True)
        persistido = telemetry().create_telemetry(payload)
    except ValueError as exc:
        if len(exc.args) > 1 and isinstance(exc.args[1], list):
            return _error_response(str(exc.args[0]), 400, campos=exc.args[1])
        return _error_response(str(exc), 400)

    return jsonify(
        {
            "message": "Telemetria recibida",
            "deviceId": persistido["deviceId"],
            "receivedAt": persistido["receivedAt"],
            "id": persistido.get("id"),
            "stored": persistido["stored"],
            "samplingActive": persistido.get("samplingActive", True),
        }
    ), 201 if persistido["stored"] else 200


@telemetry_bp.post("/api/simulator/readings")
def simular_telemetria():
    if not request.is_json:
        return _error_response("El cuerpo debe ser JSON.", 415)
    try:
        payload = request.get_json(silent=True)
        principal = g.get("account")
        device_id = "SIM-001"
        if principal:
            device_id = payload.get("deviceId") if isinstance(payload, dict) else None
            if not isinstance(device_id, str):
                return _error_response("Seleccioná un equipo registrado.", 400)
            current_app.extensions["coldtrack_operations"].owned_device(principal["id"], device_id)
        record = telemetry().simulate_telemetry(payload, device_id)
    except UseCaseError as exc:
        return _error_response(str(exc), exc.status)
    except ValueError as exc:
        return _error_response(str(exc), 400)
    return jsonify(record), 201 if record["stored"] else 200


@telemetry_bp.get("/api/devices/<device_id>/alerts")
def alertas_dispositivo(device_id):
    return jsonify({"items": telemetry().fetch_alerts(device_id)})


@telemetry_bp.get("/api/telemetry")
def listar_telemetria():
    device_id = request.args.get("deviceId")
    raw_limit = request.args.get("limit", default="50")

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return _error_response("El parámetro 'limit' debe ser un número entero.", 400)

    if limit <= 0:
        return _error_response("El parámetro 'limit' debe ser mayor que 0.", 400)

    registros = telemetry().fetch_telemetry(device_id=device_id, limit=min(limit, 500), owner_id=(g.get("account") or {}).get("id"))
    return jsonify({"count": len(registros), "items": registros}), 200


@telemetry_bp.get("/api/devices")
def listar_dispositivos():
    dispositivos = telemetry().fetch_devices(owner_id=(g.get("account") or {}).get("id"))
    return jsonify({"count": len(dispositivos), "devices": dispositivos}), 200


@telemetry_bp.get("/api/telemetry/latest/<device_id>")
def ultima_telemetria(device_id):
    lectura = telemetry().fetch_latest_telemetry(device_id)
    if lectura is None:
        return _error_response("No existen lecturas para el dispositivo.", 404, deviceId=device_id)
    return jsonify(lectura), 200


@telemetry_bp.errorhandler(404)
def not_found(_error):
    return _error_response("Recurso no encontrado.", 404)


@telemetry_bp.errorhandler(405)
def method_not_allowed(_error):
    return _error_response("Método no permitido para esta ruta.", 405)
