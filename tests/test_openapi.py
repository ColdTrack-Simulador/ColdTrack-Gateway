import re

from openapi_spec_validator import validate

from apy import app


def test_swagger_ui_serves_the_same_origin_contract():
    client = app.test_client()
    response = client.get("/apidocs/")
    assert response.status_code == 200
    assert "/apispec_1.json" in response.get_data(as_text=True)
    response = client.get("/apispec_1.json")
    assert response.status_code == 200
    specification = response.get_json()
    validate(specification)
    assert specification["openapi"] == "3.0.3"
    assert "servers" not in specification


def test_every_public_http_operation_is_in_openapi():
    specification = app.test_client().get("/apispec_1.json").get_json()
    documented = {
        (path, method.upper())
        for path, operations in specification["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    }
    implemented = {
        (re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule), method)
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/") or rule.rule == "/health"
        for method in rule.methods - {"OPTIONS", "HEAD"}
    }
    assert documented == implemented
