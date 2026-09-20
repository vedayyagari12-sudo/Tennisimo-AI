"""The generated schema must not advertise an error shape we never produce."""

from __future__ import annotations

from starlette.testclient import TestClient


def test_no_route_advertises_422(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert "422" not in operation["responses"], f"{method.upper()} {path}"


def test_error_statuses_reference_the_flat_envelope(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    ticket = schema["paths"]["/v1/uploads/ticket"]["post"]["responses"]
    for status in ("400", "401", "413", "503"):
        ref = ticket[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ErrorEnvelope")


def test_validation_helper_schemas_are_gone(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert "ErrorEnvelope" in schemas
