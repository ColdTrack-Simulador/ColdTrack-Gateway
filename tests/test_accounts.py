import re

import pytest

import apy
import database


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "test-session-only")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()
    return apy.app.test_client(), apy.app.test_client()


def seed_user(email, role="owner"):
    with database._connect() as connection:
        connection.execute(
            "INSERT INTO ct_users(email,role,name,phone,password_hash) VALUES (?,?,?,?,?)",
            (email, role, "Nombre original", "123", apy.passwords.hash("original-password")),
        )


def login(client, email, password="original-password"):
    return client.post("/api/session", json={"username": email, "password": password})


def mail_code(email):
    with database._connect() as connection:
        row = connection.execute(
            "SELECT body FROM ct_mail WHERE recipient=? ORDER BY id DESC LIMIT 1", (email,),
        ).fetchone()
    return re.search(r"\b\d{6}\b", row["body"]).group()


def confirm(client, email, code, password="password-updated"):
    return client.post("/api/password-reset/confirm", json={
        "email": email,
        "code": code,
        "password": password,
    })


@pytest.mark.parametrize("role", ["owner", "technician"])
def test_update_profile_does_not_change_account_authority(clients, role):
    client, other = clients
    email = "profile@example.com"
    seed_user(email, role)
    assert client.get("/api/profile").status_code == 401
    assert client.patch("/api/profile", json={"name": "Intruso"}).status_code == 401
    assert login(client, email).status_code == 200
    profile = client.get("/api/profile")
    assert profile.status_code == 200
    assert profile.json == {
        "id": 1, "email": email, "role": role, "name": "Nombre original", "phone": "123",
    }
    assert client.patch("/api/profile", json={"name": "Otro", "role": "admin"}).status_code == 400
    assert client.patch("/api/profile", json={"name": " "}).status_code == 400
    assert client.get("/api/profile").json == profile.json
    update = client.patch("/api/profile", json={"name": " Actualizado ", "phone": ""})
    assert update.status_code == 200
    assert update.json == {**profile.json, "name": "Actualizado", "phone": ""}
    assert login(other, email).status_code == 200
    assert other.get("/api/profile").json == update.json


def test_password_reset_is_single_use_and_invalidates_every_session(clients):
    client, other = clients
    email = "reset@example.com"
    seed_user(email)
    assert login(client, email).status_code == 200
    assert login(other, email).status_code == 200
    known = client.post("/api/password-reset", json={"email": email})
    unknown = client.post("/api/password-reset", json={"email": "unknown@example.com"})
    repeated = client.post("/api/password-reset", json={"email": email})
    assert known.status_code == unknown.status_code == repeated.status_code == 202
    assert known.json == unknown.json == repeated.json
    with database._connect() as connection:
        assert connection.execute("SELECT count(*) FROM ct_mail").fetchone()[0] == 1
    code = mail_code(email)
    assert confirm(client, email, code, "short").status_code == 400
    assert confirm(client, email, code).status_code == 204
    assert client.get("/api/profile").status_code == 401
    assert other.get("/api/session").json["authenticated"] is False
    assert other.get("/api/profile").status_code == 401
    assert confirm(client, email, code).status_code == 400
    assert login(other, email).status_code == 401
    assert login(other, email, "password-updated").status_code == 200


@pytest.mark.parametrize("condition", ["expired", "attempts"])
def test_expired_or_exhausted_code_is_rejected(clients, condition):
    client, _ = clients
    email = "attempts@example.com"
    seed_user(email)
    assert client.post("/api/password-reset", json={"email": email}).status_code == 202
    code = mail_code(email)
    if condition == "expired":
        with database._connect() as connection:
            connection.execute("UPDATE ct_password_resets SET expires=0")
    else:
        wrong = str((int(code) + 1) % 1_000_000).zfill(6)
        for _ in range(5):
            assert confirm(client, email, wrong).status_code == 400
    assert confirm(client, email, code).status_code == 400
    assert login(client, email).status_code == 200


def test_password_reset_disabled_and_rate_limited(clients, monkeypatch):
    client, _ = clients
    email = "rate@example.com"
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", False)
    assert client.post("/api/password-reset", json={"email": email}).status_code == 503
    assert confirm(client, email, "123456").status_code == 503
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    for _ in range(10):
        assert client.post("/api/password-reset", json={"email": email}).status_code == 202
    assert client.post("/api/password-reset", json={"email": email}).status_code == 429


def test_initialize_migrates_legacy_user_table(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "legacy.db"))
    with database._connect() as connection:
        connection.execute(
            """CREATE TABLE ct_users (
                id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                role TEXT NOT NULL, phone TEXT NOT NULL, password_hash TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO ct_users VALUES (1, 'legacy@example.com', 'Nombre', 'owner', '', 'legacy-hash')"
        )
    database.init_db()
    database.init_db()
    with database._connect() as connection:
        user = connection.execute("SELECT * FROM ct_users WHERE id=1").fetchone()
    assert user["session_version"] == 0
    assert user["password_hash"] == "legacy-hash"
