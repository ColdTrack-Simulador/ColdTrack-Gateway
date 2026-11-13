from __future__ import annotations

import os

import database
from hmac import compare_digest
from pathlib import Path

import click
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session, url_for
from flasgger import Swagger
from smtplib import SMTPException
from werkzeug.security import check_password_hash

from coldtrack.application import Operations, secret_digest
from coldtrack.assistant import create_assistant_blueprint
from coldtrack.http import create_operations_blueprint
from coldtrack.infrastructure import SQLiteRepository, WerkzeugPasswords
from coldtrack.smtp import SMTPMailer
from database import _connect, init_db
from routes.telemetry import telemetry_bp
from services.telemetry_service import TelemetryService


app = Flask(__name__)
frontend_dir = Path(os.environ.get(
    "COLDTRACK_FRONTEND_DIR",
    Path(__file__).resolve().parent.parent / "ColdTrack-Gateway_FRONT" / "dist",
))
username = os.environ.get("COLDTRACK_DEMO_USER")
password_hash = os.environ.get("COLDTRACK_DEMO_PASSWORD_HASH")
ingest_key = os.environ.get("COLDTRACK_INGEST_KEY")
protected = os.environ.get("COLDTRACK_REQUIRE_AUTH") == "1"
app.config.update(
    SECRET_KEY=os.environ.get("COLDTRACK_SESSION_SECRET"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.environ.get("COLDTRACK_SESSION_SECRET")) and os.environ.get("COLDTRACK_ALLOW_HTTP") != "1",
    COLDTRACK_REGISTRATION_ENABLED=os.environ.get("COLDTRACK_REGISTRATION_ENABLED") == "1",
    COLDTRACK_MAIL_WORKER_KEY=os.environ.get("COLDTRACK_MAIL_WORKER_KEY"),
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=64 * 1024,
)
if protected and not all((username, password_hash, app.secret_key, ingest_key)):
    raise RuntimeError("Faltan las credenciales requeridas de ColdTrack.")

init_db()

swagger = Swagger(
    app,
    template={
        "swagger": "2.0",
        "info": {
            "title": "ColdTrack API",
            "description": "API para monitoreo y telemetría de equipos ColdTrack.",
            "version": "1.3.0",
        },
        "basePath": "/",
    },
)

repository = SQLiteRepository(_connect)
operations = Operations(repository, WerkzeugPasswords())
app.extensions["coldtrack_telemetry"] = TelemetryService(database)
app.extensions["coldtrack_operations"] = operations
app.register_blueprint(telemetry_bp)
app.register_blueprint(create_assistant_blueprint())
app.register_blueprint(create_operations_blueprint(operations, repository))


@app.get("/")
def root_redirect():
    if (frontend_dir / "index.html").is_file():
        return send_from_directory(frontend_dir, "index.html")
    return redirect(url_for("flasgger.apidocs"))


@app.get("/apidocs")
def apidocs_redirect():
    return redirect(url_for("flasgger.apidocs"))


@app.get("/<path:asset>")
def frontend_asset(asset):
    return send_from_directory(frontend_dir, asset)


@app.before_request
def require_api_credentials():
    if not request.path.startswith("/api/"):
        return
    g.account = session.get("account")
    if request.endpoint in {"demo_session", "operations.register", "operations.verify"}:
        return
    device_id = (request.view_args or {}).get("device_id")
    auth_required = protected or app.config["COLDTRACK_REGISTRATION_ENABLED"]
    ingest = request.method == "POST" and request.path == "/api/telemetry"
    if ingest or request.endpoint == "operations.read_policy":
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if ingest:
            payload = request.get_json(silent=True)
            value = payload.get("deviceId") if isinstance(payload, dict) else None
            device_id = value.strip() if isinstance(value, str) else None
        with repository.transaction(write=False) as repo:
            managed = repo.device(device_id) if device_id else None
        if managed and scheme.lower() == "bearer" and compare_digest(secret_digest(token), managed["token_hash"]):
            g.gateway_device = device_id
            return
        if ingest:
            legacy_token = ingest_key and scheme.lower() == "bearer" and compare_digest(token.encode(), ingest_key.encode())
            if not managed and (not auth_required or legacy_token):
                return
            return jsonify({"error": "Se requiere la clave del gateway."}), 401
    if request.blueprint == "operations":
        return
    if auth_required and not g.account and not (username and session.get("user") == username):
        return jsonify({"error": "Iniciá sesión para continuar."}), 401
    if device_id:
        with repository.transaction(write=False) as repo:
            managed = repo.device(device_id)
        if (managed and (not g.account or managed["owner_id"] != g.account["id"])) or (g.account and not managed):
            return jsonify({"error": "Equipo no encontrado."}), 404


@app.after_request
def prevent_api_caching(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/session", methods=["GET", "POST", "DELETE"])
def demo_session():
    if not protected and not app.config["COLDTRACK_REGISTRATION_ENABLED"]:
        return jsonify({"authenticated": True, "required": False})
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in ("username", "password")):
            return jsonify({"error": "Ingresá usuario y contraseña."}), 400
        password, identifier = data["password"], data["username"]
        if len(password) > 256 or len(identifier) > 254:
            return jsonify({"error": "Usuario o contraseña incorrectos."}), 401
        with repository.transaction() as repo:
            allowed = repo.allow_attempt("login", request.remote_addr or "-", 30, 300)
        if not allowed:
            return jsonify({"error": "Intentá nuevamente más tarde."}), 429
        principal = operations.authenticate(identifier, password) if app.secret_key else None
        demo = username and identifier == username and password_hash and check_password_hash(password_hash, password)
        if not principal and not demo:
            return jsonify({"error": "Usuario o contraseña incorrectos."}), 401
        session.clear()
        if principal:
            session["account"] = principal
        else:
            session["user"] = username
    elif request.method == "DELETE":
        session.clear()
    principal = session.get("account")
    result = {"authenticated": bool(principal or (username and session.get("user") == username)), "required": True}
    if principal:
        result["account"] = principal
    return jsonify(result)


@app.cli.command("drain-mail")
@click.option("--limit", type=click.IntRange(1, 100), default=20)
def drain_mail(limit):
    try:
        mailer = SMTPMailer()
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    delivered = failed = 0
    for _ in range(limit):
        with repository.transaction() as repo:
            item = repo.claim_mail()
        if item is None:
            break
        try:
            mailer.send(item)
        except (OSError, SMTPException, ValueError):
            with repository.transaction() as repo:
                repo.complete_mail(item, sent=False)
            failed += 1
        else:
            with repository.transaction() as repo:
                repo.complete_mail(item, sent=True)
            delivered += 1
    click.echo(f"Enviados: {delivered}. Pendientes de reintento: {failed}.")
    if failed:
        raise click.ClickException("Al menos un correo no pudo enviarse por SMTP.")


if __name__ == "__main__":
    print()
    print("==========================================")
    print("          COLDTRACK API")
    print("==========================================")
    print("Servidor: http://127.0.0.1:5000")
    print("Endpoints:")
    print("  GET  /health")
    print("  POST /api/telemetry")
    print("  GET  /api/telemetry")
    print("  GET  /api/devices")
    print("  GET  /api/telemetry/latest/<device_id>")
    print("==========================================")

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
    )
