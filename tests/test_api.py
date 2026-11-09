import apy
import pytest
import database
from services.telemetry_service import (
    evaluate_device_state,
    should_store_telemetry,
)
@pytest.fixture(autouse=True)
def use_temporary_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()


def test_health_endpoint():
    client = apy.app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    data = response.get_json()
    assert data["service"] == "ColdTrack API"
    assert data["status"] == "ok"


def test_post_telemetry_success():
    client = apy.app.test_client()

    payload = {
        "deviceId": "CT-001",
        "estado": "NORMAL",
        "diagnostico": "SIN_FALLAS",
        "tempInterior": 21.4,
    }

    response = client.post("/api/telemetry", json=payload)

    assert response.status_code == 201
    data = response.get_json()
    assert data["deviceId"] == "CT-001"
    assert "receivedAt" in data


def test_post_telemetry_missing_required_field():
    client = apy.app.test_client()

    payload = {
        "deviceId": "CT-001",
        "estado": "NORMAL",
    }

    response = client.post("/api/telemetry", json=payload)

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Faltan campos obligatorios."
    assert "diagnostico" in data["campos"]


def test_get_latest_telemetry_for_device():
    client = apy.app.test_client()

    payload = {
        "deviceId": "CT-001",
        "estado": "NORMAL",
        "diagnostico": "SIN_FALLAS",
    }

    client.post("/api/telemetry", json=payload)

    response = client.get("/api/telemetry/latest/CT-001")

    assert response.status_code == 200
    data = response.get_json()
    assert data["deviceId"] == "CT-001"
    assert data["estado"] == "NORMAL"


def test_list_telemetry_default_limit():
    client = apy.app.test_client()

    payload = {
        "deviceId": "CT-002",
        "estado": "ALERTA",
        "diagnostico": "FALLO",
    }

    client.post("/api/telemetry", json=payload)

    response = client.get("/api/telemetry")

    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] >= 1
    assert any(item["deviceId"] == "CT-002" for item in data["items"])


def test_get_devices_list():
    client = apy.app.test_client()

    payload = {
        "deviceId": "CT-002",
        "estado": "ALERTA",
        "diagnostico": "FALLO",
    }

    client.post("/api/telemetry", json=payload)

    response = client.get("/api/devices")

    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] >= 1
    assert any(item["deviceId"] == "CT-002" for item in data["devices"])


def test_evaluate_device_state_for_normal_and_fault_states():
    assert evaluate_device_state({"estado": "NORMAL", "diagnostico": "SIN_FALLAS"}) == "normal"
    assert evaluate_device_state({"estado": "ALERTA", "diagnostico": "FALLO"}) == "alert"
    assert evaluate_device_state({"estado": "NORMAL", "diagnostico": "PRESION_ALTA"}) == "alert"


def test_should_store_telemetry_uses_fast_interval_when_alerted():
    last_reading = {"receivedAt": "2026-09-28T12:00:00+00:00"}
    now = "2026-09-28T12:00:09+00:00"

    assert should_store_telemetry({"estado": "NORMAL", "diagnostico": "SIN_FALLAS"}, last_reading, now) is False
    assert should_store_telemetry({"estado": "ALERTA", "diagnostico": "FALLO"}, last_reading, now) is True


def test_should_store_telemetry_on_recovery_immediately():
    last_reading = {"receivedAt": "2026-09-28T12:00:00+00:00", "estado": "ALERTA", "diagnostico": "FALLO"}
    now = "2026-09-28T12:00:09+00:00"

    assert should_store_telemetry({"estado": "NORMAL", "diagnostico": "SIN_FALLAS"}, last_reading, now) is True


def test_simulator_opens_one_alert_and_resolves_it_immediately():
    client = apy.app.test_client()
    endpoint = "/api/simulator/readings"
    readings = {
        "tempInterior": -18,
        "tempCondensador": 36,
        "tempSuccion": -22,
        "presionAlta": 60,
        "presionBaja": 28,
    }
    assert client.post(endpoint, json=readings).status_code == 201

    fault = client.post(endpoint, json={**readings, "presionAlta": 96, "diagnostico": "SIN_FALLAS"})
    assert fault.status_code == 201
    assert fault.json["diagnostico"] == "FALLA_PRESION_ALTA"
    assert fault.json["source"] == "simulator"

    repeated = client.post(endpoint, json={**readings, "presionAlta": 97})
    assert repeated.status_code == 200
    assert repeated.json["stored"] is False
    latest = client.get("/api/telemetry/latest/SIM-001").json
    assert latest["presionAlta"] == 97
    alerts_url = "/api/devices/SIM-001/alerts"
    alert, = client.get(alerts_url).json["items"]
    assert alert["active"] is True

    assert client.post(endpoint, json=readings).status_code == 201
    resolved, = client.get(alerts_url).json["items"]
    assert resolved["id"] == alert["id"]
    assert resolved["active"] is False
    assert resolved["resolvedAt"]
    assert client.get("/api/telemetry?deviceId=SIM-001").json["count"] == 3


def test_protected_app_separates_demo_session_from_gateway_credentials(monkeypatch):
    from werkzeug.security import generate_password_hash

    monkeypatch.setattr(apy, "protected", True)
    monkeypatch.setattr(apy, "username", "demo")
    monkeypatch.setattr(apy, "password_hash", generate_password_hash("local-test-password"))
    monkeypatch.setattr(apy, "ingest_key", "local-test-key")
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "local-test-session")
    client = apy.app.test_client()
    record = {"deviceId": "CT-001", "estado": "REFRIGERANDO", "diagnostico": "SIN_FALLAS"}

    assert client.get("/api/devices").status_code == 401
    assert client.post("/api/telemetry", json=record).status_code == 401
    assert client.post("/api/session", json={"username": "demo", "password": "wrong"}).status_code == 401
    response = client.post("/api/session", json={"username": "demo", "password": "local-test-password"})
    assert response.json["authenticated"] is True
    assert client.get("/api/devices").status_code == 200
    assert client.post("/api/telemetry", json=record).status_code == 401
    assert client.post(
        "/api/telemetry", json=record, headers={"Authorization": "Bearer local-test-key"}
    ).status_code == 201
    assert client.get("/api/telemetry/latest/CT-001").json["state"] == "normal"
    assert client.delete("/api/session").json["authenticated"] is False
    assert client.get("/api/devices").status_code == 401
