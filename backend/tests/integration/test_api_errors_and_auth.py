"""The error envelope, the auth gate, and the correlation header.

DATABASE_SETUP.md Part 3.0: every non-2xx response has the flat envelope and no
other shape. FastAPI's ``{"detail": ...}`` must never escape.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from tests.integration.conftest import (
    FakeRepository,
    FakeStorageClient,
    auth_headers,
    storage_path_for,
)

TICKET_URL = "/v1/uploads/ticket"
GOOD_TICKET_BODY = {"content_type": "video/mp4", "size_bytes": 1024, "duration_s": 6.2}


def _assert_envelope(body: dict, expected_code: str) -> None:
    """The one and only error body shape."""
    assert set(body) == {"error_code", "message", "retryable", "request_id"}
    assert "detail" not in body
    assert body["error_code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["retryable"], bool)
    assert isinstance(body["request_id"], str) and body["request_id"]


def test_validation_failure_is_flat_400_not_bare_422(client: TestClient) -> None:
    response = client.post(
        TICKET_URL,
        json={"content_type": "video/mp4", "size_bytes": 1024},  # duration_s missing
        headers=auth_headers(),
    )
    assert response.status_code == 400
    body = response.json()
    _assert_envelope(body, "invalid_request")
    assert body["retryable"] is False
    assert response.headers["X-Request-Id"] == body["request_id"]


def test_extra_key_is_rejected_not_silently_ignored(client: TestClient) -> None:
    response = client.post(
        TICKET_URL,
        json={**GOOD_TICKET_BODY, "surprise": 1},
        headers=auth_headers(),
    )
    assert response.status_code == 400
    _assert_envelope(response.json(), "invalid_request")


def test_missing_authorization_header_is_401(client: TestClient) -> None:
    response = client.post(TICKET_URL, json=GOOD_TICKET_BODY)
    assert response.status_code == 401
    _assert_envelope(response.json(), "auth_invalid_token")


def test_bad_token_is_401(client: TestClient) -> None:
    response = client.post(
        TICKET_URL, json=GOOD_TICKET_BODY, headers=auth_headers("not-a-real-token")
    )
    assert response.status_code == 401
    _assert_envelope(response.json(), "auth_invalid_token")


def test_malformed_authorization_scheme_is_401(client: TestClient) -> None:
    response = client.post(
        TICKET_URL, json=GOOD_TICKET_BODY, headers={"Authorization": "Basic abc"}
    )
    assert response.status_code == 401
    _assert_envelope(response.json(), "auth_invalid_token")


def test_unknown_path_still_returns_the_envelope(client: TestClient) -> None:
    response = client.get("/v1/nope", headers=auth_headers())
    assert response.status_code == 404
    body = response.json()
    assert "detail" not in body
    assert set(body) == {"error_code", "message", "retryable", "request_id"}


def test_unhandled_exception_becomes_flat_500(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    fake_repository.list_error = RuntimeError("boom, and the client must not see this")
    response = client.get("/v1/analyses", headers=auth_headers())
    assert response.status_code == 500
    body = response.json()
    _assert_envelope(body, "internal_error")
    assert body["retryable"] is True
    assert "boom" not in body["message"]
    # The correlation header must survive the 500 path too: without it a user's
    # "it failed" report cannot be tied to the logged traceback.
    assert response.headers["X-Request-Id"] == body["request_id"]


def test_request_id_header_is_present_on_success_too(
    client: TestClient, fake_storage: FakeStorageClient
) -> None:
    response = client.post(TICKET_URL, json=GOOD_TICKET_BODY, headers=auth_headers())
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")


def test_request_ids_differ_between_requests(client: TestClient) -> None:
    first = client.post(TICKET_URL, json=GOOD_TICKET_BODY, headers=auth_headers())
    second = client.post(TICKET_URL, json=GOOD_TICKET_BODY, headers=auth_headers())
    assert first.headers["X-Request-Id"] != second.headers["X-Request-Id"]


def test_foreign_storage_path_prefix_is_403_without_touching_storage(
    client: TestClient, fake_storage: FakeStorageClient
) -> None:
    """Stage 3 step 1: the ownership check happens BEFORE any Storage call."""
    from tests.integration.conftest import OTHER_USER_ID

    response = client.post(
        "/v1/analyses",
        json={"storage_path": storage_path_for(OTHER_USER_ID)},
        headers=auth_headers(),
    )
    assert response.status_code == 403
    _assert_envelope(response.json(), "storage_path_forbidden")
    assert fake_storage.head_calls == []
