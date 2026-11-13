from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from threading import Barrier

import pytest

import apy
import database


@pytest.fixture
def participants(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "maintenance.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "maintenance-test-only")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()

    clients = []
    with closing(database._connect()) as connection, connection:
        for user_id, role in enumerate(("owner", "technician", "owner", "technician"), start=1):
            email = f"user{user_id}@example.com"
            connection.execute(
                "INSERT INTO ct_users (id,email,name,role,phone,password_hash) VALUES (?,?,?,?,?,?)",
                (user_id, email, f"Usuario {user_id}", role, "", "test"),
            )
            client = apy.app.test_client()
            with client.session_transaction() as session:
                session["account"] = {"id": user_id, "email": email, "role": role}
                session["account_version"] = 0
            clients.append(client)
    return clients


def add_request(owner, name="Revisar compresor"):
    equipment = owner.post("/api/devices", json={"name": "Cámara"}).json
    assert owner.put("/api/technician-contacts/2").status_code == 204
    return owner.post("/api/maintenance-requests", json={
        "deviceId": equipment["deviceId"], "technicianId": 2, "description": name,
    })


def test_manual_request_and_role_transitions_with_rating(participants):
    owner, technician, other_owner, other_technician = participants
    response = add_request(owner)
    assert response.status_code == 201
    assert response.json["status"] == "requested"
    assert response.json["alertId"] is None
    path = f"/api/maintenance-requests/{response.json['id']}"
    assert apy.app.test_client().get(path).status_code == 401
    assert other_owner.get(path).status_code == other_technician.get(path).status_code == 404
    assert other_technician.patch(f"{path}/status", json={"status": "accepted"}).status_code == 404
    assert other_owner.post(f"{path}/rating", json={"score": 5}).status_code == 404
    assert technician.post(f"{path}/rating", json={"score": 5}).status_code == 403
    assert owner.post(f"{path}/rating", json={"score": 5}).status_code == 409
    assert technician.patch(f"{path}/status", json={"status": "resolved"}).status_code == 409
    assert owner.patch(f"{path}/status", json={"status": "accepted"}).status_code == 409
    assert technician.patch(f"{path}/status", json={"status": ["accepted"]}).status_code == 400

    for status in ("accepted", "in_progress", "resolved"):
        result = technician.patch(f"{path}/status", json={"status": status, "notes": "Revisado"})
        assert result.status_code == 200
        assert result.json["status"] == status
    assert owner.patch(f"{path}/status", json={"status": "closed"}).status_code == 200
    assert owner.patch(f"{path}/status", json={"status": "cancelled"}).status_code == 409

    assert owner.post(f"{path}/rating", json={"score": True}).status_code == 400
    rating = owner.post(f"{path}/rating", json={"score": 5, "comment": "Atención rápida"})
    assert rating.status_code == 201
    assert owner.post(f"{path}/rating", json={"score": 4}).status_code == 409

    detail = technician.get(path).json
    assert detail["rating"] == rating.json
    assert [(step["status"], step["actorId"]) for step in detail["history"]] == [
        ("requested", 1), ("accepted", 2), ("in_progress", 2), ("resolved", 2), ("closed", 1),
    ]
    assert owner.get("/api/reviews").json == {"items": []}
    with apy.repository.transaction(write=False) as repo:
        mail = repo.connection.execute("SELECT recipient, body FROM ct_mail").fetchone()
    assert mail["recipient"] == "user2@example.com"
    assert "Revisar compresor" in mail["body"]


def test_request_filtering_pagination_validation_and_contact_removal(participants):
    owner, technician, other_owner, other_technician = participants
    first = add_request(owner).json
    second = add_request(owner, "Otro equipo").json
    page = owner.get("/api/maintenance-requests?limit=1").json
    assert [item["id"] for item in page["items"]] == [second["id"]]
    remaining = technician.get(f"/api/maintenance-requests?limit=1&beforeId={page['nextBeforeId']}").json
    assert [item["id"] for item in remaining["items"]] == [first["id"]]
    assert remaining["nextBeforeId"] is None
    assert other_owner.get("/api/maintenance-requests").json == {"items": [], "nextBeforeId": None}
    assert owner.get("/api/maintenance-requests?limit=-1").status_code == 400
    assert owner.get("/api/maintenance-requests?beforeId=999999999999999999999").status_code == 400

    url = "/api/maintenance-requests"
    assert technician.post(url, json={}).status_code == 403
    assert other_owner.put("/api/technician-contacts/2").status_code == 204
    assert other_owner.post(url, json={
        "deviceId": first["deviceId"], "technicianId": 2, "description": "Equipo ajeno",
    }).status_code == 404
    assert owner.post(url, json={"deviceId": first["deviceId"], "technicianId": True}).status_code == 400
    assert other_technician.delete("/api/technician-contacts/2").status_code == 403
    assert owner.delete("/api/technician-contacts/2").status_code == 204
    assert owner.delete("/api/technician-contacts/2").status_code == 204
    assert owner.get("/api/technician-contacts").json == {"items": []}
    assert other_owner.get("/api/technician-contacts").json["items"][0]["id"] == 2
    assert owner.post(url, json={
        "deviceId": first["deviceId"], "technicianId": 2, "description": "Sin contacto",
    }).status_code == 404
    assert owner.patch(f"/api/maintenance-requests/{first['id']}/status", json={"status": "cancelled"}).status_code == 200
    assert technician.get(f"/api/maintenance-requests/{first['id']}").json["status"] == "cancelled"


def test_alert_requests_are_idempotent_and_visible_through_legacy_endpoint(participants):
    owner, technician, other_owner, _ = participants
    device_id = owner.post("/api/devices", json={"name": "Cámara"}).json["deviceId"]
    assert owner.put("/api/technician-contacts/2").status_code == 204
    assert other_owner.put("/api/technician-contacts/2").status_code == 204
    alert = database.add_alert(device_id, "temperature", "critical", "Alerta", "Temperatura elevada")
    values = {"alertId": alert["id"], "technicianId": 2}
    created = owner.post("/api/maintenance-requests", json=values)
    assert created.status_code == 201
    assert created.json["deviceId"] == device_id
    assert created.json["description"] == "Temperatura elevada"
    replay = owner.post("/api/reviews", json=values)
    assert replay.status_code == 200
    assert replay.json["id"] == created.json["id"]
    assert "status" not in replay.json
    assert technician.get("/api/reviews").json == {"items": [replay.json]}
    assert other_owner.post("/api/maintenance-requests", json=values).status_code == 404


def test_existing_reviews_migrate_once_and_keep_ids(participants):
    owner, technician, _, _ = participants
    device_id = owner.post("/api/devices", json={"name": "Cámara"}).json["deviceId"]
    alert = database.add_alert(device_id, "temperature", "critical", "Alerta", "Temperatura elevada")
    now = datetime.now(timezone.utc).isoformat()
    with closing(database._connect()) as connection, connection:
        connection.execute(
            """INSERT INTO ct_reviews (id, owner_id, technician_id, alert_id, device_id, description, notes, created_at)
               VALUES (42, 1, 2, ?, ?, 'Revisar cámara', '', ?)""",
            (alert["id"], device_id, now),
        )
    database.init_db()
    database.init_db()
    item = technician.get("/api/maintenance-requests/42").json
    assert item["id"] == 42
    assert item["createdAt"] == now
    assert [entry["status"] for entry in item["history"]] == ["requested"]


def concurrent_request(method, path, role, user_id, values, gate):
    client = apy.app.test_client()
    with client.session_transaction() as session:
        session["account"] = {"id": user_id, "email": f"user{user_id}@example.com", "role": role}
        session["account_version"] = 0
    gate.wait(timeout=10)
    return client.open(path, method=method, json=values).status_code


def test_competing_state_changes_and_ratings_commit_only_once(participants):
    owner, technician, _, _ = participants
    path = f"/api/maintenance-requests/{add_request(owner).json['id']}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        gate = Barrier(2)
        transitions = [
            executor.submit(concurrent_request, "PATCH", f"{path}/status", "technician", 2,
                            {"status": "accepted"}, gate)
            for _ in range(2)
        ]
        assert sorted(job.result(timeout=15) for job in transitions) == [200, 409]
        assert [event["status"] for event in owner.get(path).json["history"]] == ["requested", "accepted"]
        for status in ("in_progress", "resolved"):
            assert technician.patch(f"{path}/status", json={"status": status}).status_code == 200
        assert owner.patch(f"{path}/status", json={"status": "closed"}).status_code == 200

        gate = Barrier(2)
        ratings = [
            executor.submit(concurrent_request, "POST", f"{path}/rating", "owner", 1, {"score": score}, gate)
            for score in (4, 5)
        ]
        assert sorted(job.result(timeout=15) for job in ratings) == [201, 409]
    with apy.repository.transaction(write=False) as repo:
        count = repo.connection.execute("SELECT COUNT(*) FROM ct_maintenance_ratings").fetchone()[0]
    assert count == 1
