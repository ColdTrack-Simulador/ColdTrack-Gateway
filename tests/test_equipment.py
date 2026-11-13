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
