import apy
import database


def test_questions_are_scoped_to_authenticated_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "assistant.db"))
    monkeypatch.setitem(apy.app.config, "SECRET_KEY", "assistant-tests")
    monkeypatch.setitem(apy.app.config, "COLDTRACK_REGISTRATION_ENABLED", True)
    database.init_db()
    with database._connect() as connection:
        connection.execute(
            "INSERT INTO ct_users(email,name,role,phone,password_hash) VALUES(?,?,'owner','','unused')",
            ("owner@example.test", "Owner"),
        )
    client = apy.app.test_client()
    path = "/api/assistant/questions"
    assert client.post(path, json={"question": "alertas"}).status_code == 401
    with client.session_transaction() as session:
        session["account_version"] = 0
        session["account"] = {"id": 1, "role": "owner", "email": "owner@example.test"}
    assert client.post(path, json={"question": " "}).status_code == 400
    assert client.post(path, json={"question": "x" * 501}).status_code == 400
    result = client.post(path, json={"question": "¿Cómo pauso la captura de un sensor?"})
    assert result.status_code == 200 and result.json["answered"]
    assert client.post(path, json={"question": "hola"}).json["answered"] is False
