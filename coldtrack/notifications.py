from flask import Blueprint, jsonify

from coldtrack.application import UseCaseError
from coldtrack.domain import CHANNELS
from coldtrack.http import actor, payload


ALERT_CODES = {f"{channel}:{bound}" for channel in CHANNELS for bound in ("min", "max")} | {
    "POSIBLE_PROBLEMA_CONDENSACION", "POSIBLE_PERDIDA_CAPACIDAD_FRIGORIFICA",
}


def create_notifications_blueprint(repository, operations):
    bp = Blueprint("notifications", __name__)

    @bp.errorhandler(UseCaseError)
    def handle_error(error):
        return jsonify({"error": str(error)}), error.status

    @bp.get("/api/devices/<device_id>/notification-preferences")
    def read_preferences(device_id):
        operations.owned_device(actor("owner"), device_id)
        with repository.transaction(write=False) as data:
            return jsonify(data.notification_preferences(device_id))

    @bp.put("/api/devices/<device_id>/notification-preferences")
    def replace_preferences(device_id):
        operations.owned_device(actor("owner"), device_id)
        preferences = payload()
        enabled = preferences.get("emailEnabled")
        muted = preferences.get("mutedCodes")
        if (set(preferences) != {"emailEnabled", "mutedCodes"} or type(enabled) is not bool
                or not isinstance(muted, list) or len(muted) > len(ALERT_CODES)
                or any(not isinstance(code, str) or code not in ALERT_CODES for code in muted)):
            raise UseCaseError("Enviá emailEnabled y mutedCodes con códigos de alerta válidos.")
        result = {"emailEnabled": enabled, "mutedCodes": sorted(set(muted))}
        with repository.transaction() as data:
            data.save_notification_preferences(device_id, result)
        return jsonify(result)

    return bp
