from flask import Blueprint, g, jsonify, request

from coldtrack.application import UseCaseError
from coldtrack.equipment import Equipment
from coldtrack.http import actor, payload


def create_equipment_blueprint(repository):
    bp = Blueprint("equipment", __name__)
    equipment = Equipment(repository)

    @bp.errorhandler(UseCaseError)
    def handle_error(error):
        return jsonify({"error": str(error)}), error.status

    @bp.post("/api/devices/pairing")
    def provision():
        return jsonify(equipment.provision(actor("owner"), payload())), 201

    @bp.post("/api/devices/claim")
    def claim():
        return jsonify(equipment.claim(actor("owner"), payload())), 200

    @bp.get("/api/devices/<device_id>")
    def detail(device_id):
        return jsonify(equipment.detail(actor("owner"), device_id))

    @bp.get("/api/devices/<device_id>/usage")
    def usage(device_id):
        days = request.args.get("days", "7")
        if not days.isascii() or not days.isdecimal() or len(days) > 2:
            raise UseCaseError("days debe estar entre 1 y 30.")
        return jsonify(equipment.usage(actor("owner"), device_id, int(days)))

    @bp.put("/api/devices/<device_id>/capture-ack")
    def capture_ack(device_id):
        if g.get("gateway_device") != device_id:
            raise UseCaseError("Se requiere la clave del gateway del equipo.", 401)
        equipment.acknowledge(device_id, payload())
        return "", 204

    return bp
