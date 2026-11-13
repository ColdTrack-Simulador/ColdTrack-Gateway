from hmac import compare_digest

from flask import Blueprint, current_app, g, jsonify, request

from coldtrack.application import UseCaseError


def payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise UseCaseError("Enviá un objeto JSON.")
    return value


def actor(role=None):
    principal = g.get("account")
    if not principal:
        raise UseCaseError("Iniciá sesión con una cuenta registrada.", 401)
    if role and principal["role"] != role:
        raise UseCaseError("Tu cuenta no tiene acceso a esta operación.", 403)
    return principal["id"]


def create_operations_blueprint(operations, repository):
    bp = Blueprint("operations", __name__)

    @bp.errorhandler(UseCaseError)
    def handle_error(error):
        return jsonify({"error": str(error)}), error.status

    def registration_key():
        if not current_app.config["COLDTRACK_REGISTRATION_ENABLED"] or not current_app.secret_key:
            raise UseCaseError("El registro todavía no está habilitado.", 503)
        return current_app.secret_key

    def require_mail_worker():
        expected = current_app.config.get("COLDTRACK_MAIL_WORKER_KEY")
        scheme, _, key = request.headers.get("Authorization", "").partition(" ")
        if not expected or scheme.lower() != "bearer" or not compare_digest(expected.encode(), key.encode()):
            raise UseCaseError("Se requiere la clave del servicio de correo.", 401)

    @bp.post("/api/registration")
    def register():
        key = registration_key()
        with repository.transaction() as data:
            allowed = data.allow_attempt("registration", request.remote_addr or "-", 10, 3600)
        if not allowed:
            raise UseCaseError("Intentá nuevamente más tarde.", 429)
        registration_id = operations.register(payload(), key)
        return jsonify({"message": "Si corresponde, enviaremos un código de verificación.",
                        "registrationId": registration_id}), 202

    @bp.post("/api/registration/verify")
    def verify():
        operations.verify(payload(), registration_key())
        return "", 204

    @bp.post("/api/devices")
    def create_device():
        return jsonify(operations.create_device(actor("owner"), payload())), 201

    @bp.get("/api/devices/<device_id>/policy")
    def read_policy(device_id):
        with repository.transaction(write=False) as data:
            device = data.device(device_id)
        if g.get("gateway_device") != device_id:
            owner = actor("owner")
            if not device or device["owner_id"] != owner:
                raise UseCaseError("Equipo no encontrado.", 404)
        return jsonify(operations.policy_response(device["policy"]))

    @bp.patch("/api/devices/<device_id>/policy")
    def update_policy(device_id):
        return jsonify(operations.update_policy(actor("owner"), device_id, payload()))

    @bp.get("/api/technicians")
    def technicians():
        owner = actor("owner")
        with repository.transaction(write=False) as data:
            items = data.technicians(owner, contacts_only=False)
        return jsonify({"items": items})

    @bp.get("/api/technician-contacts")
    def contacts():
        owner = actor("owner")
        with repository.transaction(write=False) as data:
            items = data.technicians(owner, contacts_only=True)
        return jsonify({"items": items})

    @bp.put("/api/technician-contacts/<int:technician_id>")
    def save_contact(technician_id):
        operations.save_contact(actor("owner"), technician_id)
        return "", 204

    @bp.post("/api/mail/claim")
    def claim_mail():
        require_mail_worker()
        with repository.transaction() as data:
            item = data.claim_mail()
        if not item:
            return "", 204
        return jsonify({key: item[key] for key in ("id", "lease", "recipient", "subject", "body")})

    @bp.post("/api/mail/<int:mail_id>/ack")
    def acknowledge_mail(mail_id):
        require_mail_worker()
        data = payload()
        lease, sent = data.get("lease"), data.get("sent")
        if not isinstance(lease, str) or len(lease) > 64 or type(sent) is not bool:
            raise UseCaseError("Enviá lease y sent.")
        with repository.transaction() as repo:
            if not repo.complete_mail({"id": mail_id, "lease": lease}, sent=sent):
                raise UseCaseError("La reserva del correo venció o ya se confirmó.", 409)
        return "", 204

    return bp
