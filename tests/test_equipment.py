from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

import apy
import database


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "equipment.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "equipment-test-only")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()
    with database._connect() as connection:
        connection.executemany(
            "INSERT INTO ct_users(email,name,role,phone,password_hash) VALUES(?,?,'owner','','unused')",
            [("owner@example.test", "Owner"), ("other@example.test", "Other")],
        )

    def signed_in(actor):
        client = apy.app.test_client()
        with client.session_transaction() as session:
            session["account_version"] = 0
            session["account"] = {"id": actor, "role": "owner", "email": "test@example.test"}
        return client

    return signed_in


def test_pairing_is_scoped_and_consumed_atomically(clients):
    owner = clients(1)
    response = owner.post("/api/devices/pairing", json={"name": "Cámara"})
    assert response.status_code == 201
    body = {"pairingCode": response.json["pairingCode"]}
    assert clients(2).post("/api/devices/claim", json=body).status_code == 404

    def claim(_):
        return clients(1).post("/api/devices/claim", json=body)

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(claim, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 404]
    device = next(response.json for response in responses if response.status_code == 200)
    assert device["gatewayToken"]
    assert owner.get("/api/devices/" + device["deviceId"]).json["status"] == "offline"


def test_usage_and_policy_ack(clients):
    owner = clients(1)
    device = owner.post("/api/devices", json={"name": "Cámara"}).json
    base = "/api/devices/" + device["deviceId"]
    assert clients(2).get(base + "/usage").status_code == 404
    assert owner.get(base + "/usage?days=31").status_code == 400
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    with database._connect() as connection:
        connection.executemany(
            """INSERT INTO telemetry_readings(device_id,compresor,raw_payload,received_at)
               VALUES(?,?,'{}',?)""",
            [(device["deviceId"], value, (now + timedelta(seconds=offset)).isoformat())
             for offset, value in [(0, 50), (60, 50), (120, 0), (500, 50)]],
        )
    usage = owner.get(base + "/usage").json
    assert (usage["observedSeconds"], usage["runningSeconds"]) == (120, 60)
    gateway = apy.app.test_client()
    headers = {"Authorization": "Bearer " + device["gatewayToken"]}
    revision = gateway.get(base + "/policy", headers=headers).json["revision"]
    ack = {"revision": revision, "applied": True}
    assert owner.put(base + "/capture-ack", json=ack).status_code == 401
    assert gateway.put(base + "/capture-ack", headers=headers, json=ack).status_code == 204
    assert owner.get(base + "/policy").json["captureAck"]["applied"] is True
    assert owner.patch(base + "/policy", json={"enabled": False}).status_code == 200
    assert gateway.put(base + "/capture-ack", headers=headers, json=ack).status_code == 409

def test_expired_pairing_cannot_create_a_device(clients):
    owner = clients(1)
    qr = owner.post("/api/devices/pairing", json={"name": "Cámara"}).json
    with database._connect() as connection:
        connection.execute("UPDATE ct_pairing SET expires=0")
    assert owner.post("/api/devices/claim", json={"pairingCode": qr["pairingCode"]}).status_code == 404
    assert owner.get("/api/devices").json["devices"] == []


def test_gateway_ack_requires_matching_credential_and_current_revision(clients):
    owner = clients(1)
    first = owner.post("/api/devices", json={"name": "Primero"}).json
    second = owner.post("/api/devices", json={"name": "Segundo"}).json
    path = f"/api/devices/{first['deviceId']}"
    gateway = apy.app.test_client()
    policy = owner.get(f"{path}/policy").json
    ack = {"revision": policy["revision"], "applied": False}
    wrong = {"Authorization": "Bearer " + second["gatewayToken"]}
    right = {"Authorization": "Bearer " + first["gatewayToken"]}
    assert gateway.put(f"{path}/capture-ack", headers=wrong, json=ack).status_code == 401
    assert gateway.put(f"{path}/capture-ack", headers=right, json={**ack, "applied": "false"}).status_code == 400
    assert gateway.put(f"{path}/capture-ack", headers=right, json=ack).status_code == 204
    result = owner.get(f"{path}/policy").json["captureAck"]
    assert result["applied"] is False
    assert datetime.fromisoformat(result["acknowledgedAt"]).utcoffset() == timedelta(0)
    assert owner.patch(f"{path}/policy", json={"channels": ["invalid"]}).status_code == 400
    assert owner.get(f"{path}/policy").json["revision"] == policy["revision"]
