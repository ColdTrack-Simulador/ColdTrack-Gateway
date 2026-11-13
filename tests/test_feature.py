import apy
import database


def test_preferences_preserve_alerts_and_scope_access(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "notifications.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "notifications-tests")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()
    with database._connect() as conn:
        conn.execute("INSERT INTO ct_users(email,name,role,phone,password_hash) VALUES('test@example.com','Test','owner','','x')")
    owner = apy.app.test_client()
    with owner.session_transaction() as session:
        session["account"] = {"id": 1, "role": "owner"}
    device = owner.post("/api/devices", json={"name": "Cámara"}).json["deviceId"]
    path = f"/api/devices/{device}/notification-preferences"
    assert apy.app.test_client().get(path).status_code == 401
    assert owner.get(path).json == {"emailEnabled": True, "mutedCodes": []}
    assert owner.put(path, json={"emailEnabled": 0, "mutedCodes": []}).status_code == 400
    assert owner.put(path, json={"emailEnabled": True, "mutedCodes": ["inventada"]}).status_code == 400
    assert owner.put(path, json={"emailEnabled": True, "mutedCodes": ["tempInterior:max"]}).status_code == 200
    assert owner.post("/api/simulator/readings", json={"deviceId": device, "tempInterior": 20}).status_code == 201
    assert any(alert["type"] == "tempInterior:max" for alert in owner.get(f"/api/devices/{device}/alerts").json["items"])
    with database._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ct_mail").fetchone()[0] == 0
