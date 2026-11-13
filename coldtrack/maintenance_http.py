from flask import Blueprint, g, jsonify, request

from coldtrack.application import UseCaseError
from coldtrack.http import actor, payload
from coldtrack.maintenance_application import positive_id


def legacy_review(item):
    return {key: value for key, value in item.items() if key not in {"status", "updatedAt"}}


def pagination():
    try:
        limit = int(request.args.get("limit", "50"))
        before = request.args.get("beforeId")
        before = positive_id(int(before)) if before is not None else None
    except ValueError as error:
        raise UseCaseError("Revisá limit y beforeId.") from error
    if not 1 <= limit <= 100:
        raise UseCaseError("limit debe estar entre 1 y 100.")
    return limit, before


def create_maintenance_blueprint(maintenance):
    bp = Blueprint("maintenance", __name__)

    @bp.errorhandler(UseCaseError)
    def handle_error(error):
        return jsonify({"error": str(error)}), error.status

    @bp.post("/api/maintenance-requests")
    def create_request():
        item, created = maintenance.create(actor("owner"), payload())
        return jsonify(item), 201 if created else 200

    @bp.get("/api/maintenance-requests")
    def list_requests():
        actor()
        limit, before = pagination()
        items = maintenance.list_requests(g.account, limit + 1, before)
        next_before = items[limit - 1]["id"] if len(items) > limit else None
        return jsonify({"items": items[:limit], "nextBeforeId": next_before})

    @bp.get("/api/maintenance-requests/<int:request_id>")
    def get_request(request_id):
        actor()
        return jsonify(maintenance.get_request(g.account, request_id))

    @bp.patch("/api/maintenance-requests/<int:request_id>/status")
    def update_status(request_id):
        actor()
        return jsonify(maintenance.transition(g.account, request_id, payload()))

    @bp.post("/api/maintenance-requests/<int:request_id>/rating")
    def rate_request(request_id):
        actor("owner")
        return jsonify(maintenance.rate(g.account, request_id, payload())), 201

    @bp.delete("/api/technician-contacts/<int:technician_id>")
    def remove_contact(technician_id):
        maintenance.remove_contact(actor("owner"), technician_id)
        return "", 204

    @bp.post("/api/reviews")
    def create_review():
        owner = actor("owner")
        data = payload()
        if "alertId" not in data:
            raise UseCaseError("Revisá el identificador de alerta.")
        values = {key: data[key] for key in ("alertId", "technicianId", "notes") if key in data}
        item, created = maintenance.create(owner, values)
        return jsonify(legacy_review(item)), 201 if created else 200

    @bp.get("/api/reviews")
    def reviews():
        actor()
        items = maintenance.list_requests(g.account, 100, None, alerts_only=True)
        return jsonify({"items": [legacy_review(item) for item in items]})

    return bp
