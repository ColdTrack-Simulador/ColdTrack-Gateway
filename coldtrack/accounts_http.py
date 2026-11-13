from flask import Blueprint, current_app, jsonify, request, session

from coldtrack.application import UseCaseError
from coldtrack.http import actor, payload


def create_accounts_blueprint(accounts, repository):
    bp = Blueprint("accounts", __name__)

    @bp.errorhandler(UseCaseError)
    def handle_error(error):
        return jsonify({"error": str(error)}), error.status

    def reset_key():
        if not current_app.config["COLDTRACK_REGISTRATION_ENABLED"] or not current_app.secret_key:
            raise UseCaseError("La recuperación todavía no está habilitada.", 503)
        return current_app.secret_key

    def require_attempt(action, maximum):
        with repository.transaction() as data:
            allowed = data.allow_attempt(action, request.remote_addr or "-", maximum, 3600)
        if not allowed:
            raise UseCaseError("Intentá nuevamente más tarde.", 429)

    @bp.get("/api/profile")
    def profile():
        return jsonify(accounts.profile(actor()))

    @bp.patch("/api/profile")
    def update_profile():
        return jsonify(accounts.update_profile(actor(), payload()))

    @bp.post("/api/password-reset")
    def request_reset():
        key = reset_key()
        require_attempt("password-reset-request", 10)
        accounts.request_reset(payload(), key)
        return jsonify({"message": "Si corresponde, enviaremos un código de recuperación."}), 202

    @bp.post("/api/password-reset/confirm")
    def confirm_reset():
        key = reset_key()
        require_attempt("password-reset-confirm", 30)
        accounts.confirm_reset(payload(), key)
        session.clear()
        return "", 204

    return bp
