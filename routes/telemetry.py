from __future__ import annotations

from flask import Blueprint, jsonify, request
from database import list_alerts

from services.telemetry_service import (
    create_telemetry,
    fetch_devices,
    fetch_latest_telemetry,
    fetch_telemetry,
    simulate_telemetry,
)

telemetry_bp = Blueprint("telemetry_bp", __name__)


def _error_response(message: str, status_code: int, **extra):
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status_code


@telemetry_bp.get("/health")
def health():
    """
    Health check
    ---
    tags:
      - System
    responses:
      200:
        description: API health status
        examples:
          application/json:
            service: ColdTrack API
            status: ok
            version: 1.3.0
            storage: sqlite
            architecture: layered
    """
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
    """
    Receive telemetry payload
    ---
    tags:
      - Telemetry
    consumes:
      - application/json
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - deviceId
            - estado
            - diagnostico
          properties:
            deviceId:
              type: string
              example: CT-001
            estado:
              type: string
              example: NORMAL
            diagnostico:
              type: string
              example: SIN_FALLAS
            tempInterior:
              type: number
              example: 21.4
    responses:
      201:
        description: Telemetry received successfully
      400:
        description: Invalid request payload
    """
    if not request.is_json:
        return _error_response("El cuerpo debe ser JSON.", 415)

    try:
        payload = request.get_json(silent=True)
        persistido = create_telemetry(payload)
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
        }
    ), 201 if persistido["stored"] else 200


@telemetry_bp.post("/api/simulator/readings")
def simular_telemetria():
    if not request.is_json:
        return _error_response("El cuerpo debe ser JSON.", 415)
    try:
        record = simulate_telemetry(request.get_json(silent=True))
    except ValueError as exc:
        return _error_response(str(exc), 400)
    return jsonify(record), 201 if record["stored"] else 200


@telemetry_bp.get("/api/devices/<device_id>/alerts")
def alertas_dispositivo(device_id):
    return jsonify({"items": list_alerts(device_id)})


@telemetry_bp.get("/api/telemetry")
def listar_telemetria():
    """
    List telemetry readings
    ---
    tags:
      - Telemetry
    parameters:
      - in: query
        name: deviceId
        type: string
        required: false
      - in: query
        name: limit
        type: integer
        required: false
        default: 50
    responses:
      200:
        description: Telemetry readings list
    """
    device_id = request.args.get("deviceId")
    raw_limit = request.args.get("limit", default="50")

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return _error_response("El parámetro 'limit' debe ser un número entero.", 400)

    if limit <= 0:
        return _error_response("El parámetro 'limit' debe ser mayor que 0.", 400)

    registros = fetch_telemetry(device_id=device_id, limit=min(limit, 500))
    return jsonify({"count": len(registros), "items": registros}), 200


@telemetry_bp.get("/api/devices")
def listar_dispositivos():
    """
    List devices and their latest state
    ---
    tags:
      - Devices
    responses:
      200:
        description: Device list
    """
    dispositivos = fetch_devices()
    return jsonify({"count": len(dispositivos), "devices": dispositivos}), 200


@telemetry_bp.get("/api/telemetry/latest/<device_id>")
def ultima_telemetria(device_id):
    """
    Get latest telemetry for a device
    ---
    tags:
      - Telemetry
    parameters:
      - in: path
        name: device_id
        type: string
        required: true
    responses:
      200:
        description: Latest telemetry for the requested device
      404:
        description: No telemetry exists for the requested device
    """
    lectura = fetch_latest_telemetry(device_id)
    if lectura is None:
        return _error_response("No existen lecturas para el dispositivo.", 404, deviceId=device_id)
    return jsonify(lectura), 200


@telemetry_bp.errorhandler(404)
def not_found(_error):
    return _error_response("Recurso no encontrado.", 404)


@telemetry_bp.errorhandler(405)
def method_not_allowed(_error):
    return _error_response("Método no permitido para esta ruta.", 405)
