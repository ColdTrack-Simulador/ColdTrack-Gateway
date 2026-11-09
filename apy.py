from __future__ import annotations

import os
from hmac import compare_digest
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from flasgger import Swagger
from werkzeug.security import check_password_hash

from database import init_db
from routes.telemetry import telemetry_bp


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
    SESSION_COOKIE_SECURE=protected and os.environ.get("COLDTRACK_ALLOW_HTTP") != "1",
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

app.register_blueprint(telemetry_bp)


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
    if not protected or not request.path.startswith("/api/") or request.path == "/api/session":
        return
    if request.method == "POST" and request.path == "/api/telemetry":
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer" and compare_digest(token.encode(), ingest_key.encode()):
            return
        return jsonify({"error": "Se requiere la clave del gateway."}), 401
    if session.get("user") != username:
        return jsonify({"error": "Iniciá sesión para continuar."}), 401


@app.after_request
def prevent_api_caching(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/session", methods=["GET", "POST", "DELETE"])
def demo_session():
    if not protected:
        return jsonify({"authenticated": True, "required": False})
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("password"), str):
            return jsonify({"error": "Ingresá usuario y contraseña."}), 400
        if len(data["password"]) > 256 or not check_password_hash(password_hash, data["password"]) or data.get("username") != username:
            return jsonify({"error": "Usuario o contraseña incorrectos."}), 401
        session.clear()
        session["user"] = username
    elif request.method == "DELETE":
        session.clear()
    return jsonify({"authenticated": session.get("user") == username, "required": True})


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
