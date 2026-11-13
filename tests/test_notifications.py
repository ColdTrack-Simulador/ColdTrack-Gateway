import apy
import database


def test_preferences_preserve_alerts_and_scope_access(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "notifications.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "notifications-tests")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()
    with database._connect() as conn:
        conn.execute("INSERT INTO ct_users(email,name,role,phone,password_hash) VALUES('test@example.com','Test','owner','','x')")
        conn.execute(
            "INSERT INTO ct_users(email,name,role,phone,password_hash) VALUES(?,?,'owner','','unused')",
            ("other@example.test", "Other owner"),
        )
    other = apy.app.test_client()
    with other.session_transaction() as session:
        session["account_version"] = 0
        session["account"] = {"id": 2, "role": "owner", "email": "other@example.test"}
    owner = apy.app.test_client()
    with owner.session_transaction() as session:
        session["account_version"] = 0
        session["account"] = {"id": 1, "role": "owner", "email": "test@example.com"}
    device = owner.post("/api/devices", json={"name": "Cámara"}).json["deviceId"]
    path = f"/api/devices/{device}/notification-preferences"
    assert apy.app.test_client().get(path).status_code == 401
    assert other.get(path).status_code == 404
    assert other.put(path, json={"emailEnabled": False, "mutedCodes": []}).status_code == 404
    assert owner.get(path).json == {"emailEnabled": True, "mutedCodes": []}
    assert owner.put(path, json={"emailEnabled": 0, "mutedCodes": []}).status_code == 400
    assert owner.put(path, json={"emailEnabled": True, "mutedCodes": ["inventada"]}).status_code == 400
    assert owner.put(path, json={"emailEnabled": True, "mutedCodes": ["tempInterior:max"]}).status_code == 200
    readings = {
        "deviceId": device, "tempInterior": 20, "tempCondensador": 38,
        "tempSuccion": -22, "presionAlta": 62, "presionBaja": 28,
    }
    assert owner.post("/api/simulator/readings", json=readings).status_code == 201
    assert any(alert["type"] == "tempInterior:max" for alert in owner.get(f"/api/devices/{device}/alerts").json["items"])
    with database._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ct_mail").fetchone()[0] == 0
