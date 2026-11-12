from datetime import datetime, timezone
from re import search

import apy
import database
from coldtrack.domain import sampling_active


def register(client, email, role):
    credentials = {"username": email, "password": "password-for-tests"}
    registered = client.post("/api/registration", json={
        "email": email, "name": role, "role": role, "password": credentials["password"],
        "phone": "+54 381 5550100",
    })
    assert registered.status_code == 202
    assert client.post("/api/session", json=credentials).status_code == 401
    with database._connect() as connection:
        mail = connection.execute("SELECT body FROM ct_mail WHERE recipient=? ORDER BY id DESC", (email,)).fetchone()
    code = search(r"\b\d{6}\b", mail["body"]).group()
    assert client.post("/api/registration/verify", json={
        "email": email, "code": code, "registrationId": registered.json["registrationId"],
    }).status_code == 204
    login = client.post("/api/session", json=credentials)
    assert login.status_code == 200
    return login.json["account"]


def test_owner_device_policy_alert_mail_and_technician_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "operations.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "test-session-only")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()
    owner = apy.app.test_client()
    technician = apy.app.test_client()
    register(owner, "owner@example.com", "owner")
    tech = register(technician, "tech@example.com", "technician")
    assert owner.put(f"/api/technician-contacts/{tech['id']}").status_code == 204
    assert owner.get("/api/technician-contacts").json["items"][0]["email"] == "tech@example.com"

    created = owner.post("/api/devices", json={"name": "Cámara de ensayo"})
    assert created.status_code == 201
    device_id = created.json["deviceId"]
    headers = {"Authorization": f"Bearer {created.json['gatewayToken']}"}
    gateway = apy.app.test_client()
    policy = f"/api/devices/{device_id}/policy"
    assert technician.get(f"/api/telemetry/latest/{device_id}").status_code == 404
    assert technician.get("/api/devices").json["count"] == 0
    assert technician.patch(policy, json={"enabled": False}).status_code == 403

    assert owner.patch(policy, json={"enabled": False}).status_code == 200
    assert gateway.get(policy, headers=headers).json["samplingActive"] is False
    reading = {"deviceId": device_id, "estado": "NORMAL", "diagnostico": "SIN_FALLAS",
               "presionAlta": 98, "tempCondensador": 70}
    assert gateway.post("/api/telemetry", json=reading).status_code == 401
    paused = gateway.post("/api/telemetry", json=reading, headers=headers)
    assert paused.status_code == 200 and paused.json["samplingActive"] is False
    assert owner.get(f"/api/telemetry/latest/{device_id}").status_code == 404

    assert owner.patch(policy, json={"enabled": True}).status_code == 200
    assert gateway.post("/api/telemetry", json=reading, headers=headers).status_code == 201
    assert gateway.post("/api/telemetry", json=reading, headers=headers).status_code == 200
    alerts = owner.get(f"/api/devices/{device_id}/alerts").json["items"]
    assert {row["type"] for row in alerts} == {
        "presionAlta:max", "tempCondensador:max", "POSIBLE_PROBLEMA_CONDENSACION",
    }
    failure = next(row for row in alerts if row["type"] == "POSIBLE_PROBLEMA_CONDENSACION")
    review = {"alertId": failure["id"], "technicianId": tech["id"], "notes": "Revisar el equipo."}
    assert owner.post("/api/reviews", json=review).status_code == 201
    assert owner.post("/api/reviews", json=review).status_code == 200
    assert technician.get("/api/reviews").json["items"][0]["description"] == failure["description"]

    sent = []
    class Mailer:
        def send(self, item):
            sent.append(item)
    monkeypatch.setattr(apy, "SMTPMailer", Mailer)
    delivery = apy.app.test_cli_runner().invoke(args=["drain-mail"])
    assert delivery.exit_code == 0, delivery.output
    assert [item["recipient"] for item in sent].count("owner@example.com") == 3
    assert [item["recipient"] for item in sent].count("tech@example.com") == 1
    assert apy.app.test_cli_runner().invoke(args=["drain-mail"]).exit_code == 0
    assert len(sent) == 4

    assert gateway.post("/api/telemetry", json={**reading, "presionAlta": 60, "tempCondensador": 36}, headers=headers).status_code == 201
    assert all(not alert["active"] for alert in owner.get(f"/api/devices/{device_id}/alerts").json["items"])

    assert owner.patch(policy, json={"channels": ["tempInterior"], "limits": {"tempInterior": {"max": 12}}}).status_code == 200
    simulation = owner.post("/api/simulator/readings", json={
        "deviceId": device_id, "tempInterior": 10, "tempCondensador": 70,
        "tempSuccion": -22, "presionAlta": 98, "presionBaja": 20,
    })
    assert simulation.status_code in (200, 201)
    assert simulation.json["state"] == "normal"
    assert "presionAlta" not in simulation.json


def test_weekly_window_crosses_midnight_and_week_boundary():
    sunday_night = [{"day": 6, "start": 23 * 60, "end": 60}]
    assert sampling_active(True, sunday_night, datetime(2026, 11, 16, 0, 30, tzinfo=timezone.utc))
    assert not sampling_active(True, sunday_night, datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc))
    assert not sampling_active(False, sunday_night, datetime(2026, 11, 16, 0, 30, tzinfo=timezone.utc))


def test_external_mail_worker_requires_its_own_key_and_acknowledges_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "mail.db"))
    monkeypatch.setitem(apy.app.config, "COLDTRACK_MAIL_WORKER_KEY", "worker-test-only")
    database.init_db()
    with apy.repository.transaction() as repo:
        repo.queue_mail("owner@example.com", "Alerta", "Revisá el equipo.")

    client = apy.app.test_client()
    assert client.post("/api/mail/claim").status_code == 401
    headers = {"Authorization": "Bearer worker-test-only"}
    item = client.post("/api/mail/claim", headers=headers).json
    assert item["recipient"] == "owner@example.com"
    assert client.post("/api/mail/claim", headers=headers).status_code == 204
    ack = f"/api/mail/{item['id']}/ack"
    assert client.post(ack, headers=headers, json={"lease": "wrong", "sent": True}).status_code == 409
    assert client.post(ack, headers=headers, json={"lease": item["lease"], "sent": True}).status_code == 204
    assert client.post("/api/mail/claim", headers=headers).status_code == 204
    with database._connect() as connection:
        assert connection.execute("SELECT body FROM ct_mail").fetchone()["body"] == ""
