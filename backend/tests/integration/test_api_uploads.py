"""Stage 1 — ``POST /v1/uploads/ticket``."""

from __future__ import annotations

from uuid import UUID

from starlette.testclient import TestClient

from app.api.errors import ApiError
from app.config import MAX_UPLOAD_BYTES
from app.models.enums import ErrorCode
from tests.integration.conftest import USER_ID, FakeStorageClient, auth_headers

TICKET_URL = "/v1/uploads/ticket"


def test_ticket_path_is_server_chosen_and_user_prefixed(
    client: TestClient, fake_storage: FakeStorageClient
) -> None:
    response = client.post(
        TICKET_URL,
        json={"content_type": "video/mp4", "size_bytes": 18_452_113, "duration_s": 6.24},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"storage_path", "upload_url", "upload_token", "expires_at"}

    prefix = f"swing-videos/{USER_ID}/"
    assert body["storage_path"].startswith(prefix)
    name = body["storage_path"][len(prefix) :]
    stem, _, extension = name.partition(".")
    assert extension == "mp4"
    UUID(stem)  # server-chosen uuid4, not client-proposed
    assert fake_storage.signed_calls == [body["storage_path"]]


def test_quicktime_maps_to_mov(client: TestClient) -> None:
    response = client.post(
        TICKET_URL,
        json={"content_type": "video/quicktime", "size_bytes": 1024, "duration_s": 3.0},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["storage_path"].endswith(".mov")


def test_unsupported_content_type_has_its_own_code(client: TestClient) -> None:
    response = client.post(
        TICKET_URL,
        json={"content_type": "video/avi", "size_bytes": 1024, "duration_s": 3.0},
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_content_type"


def test_declared_size_over_the_cap_is_413(client: TestClient) -> None:
    response = client.post(
        TICKET_URL,
        json={
            "content_type": "video/mp4",
            "size_bytes": MAX_UPLOAD_BYTES + 1,
            "duration_s": 3.0,
        },
        headers=auth_headers(),
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error_code"] == "file_too_large"
    assert body["retryable"] is False


def test_exactly_the_cap_is_accepted(client: TestClient) -> None:
    response = client.post(
        TICKET_URL,
        json={"content_type": "video/mp4", "size_bytes": MAX_UPLOAD_BYTES, "duration_s": 3.0},
        headers=auth_headers(),
    )
    assert response.status_code == 200


def test_storage_unavailable_is_503_and_retryable(
    client: TestClient, fake_storage: FakeStorageClient
) -> None:
    fake_storage.signed_error = ApiError(ErrorCode.STORAGE_UNAVAILABLE)
    response = client.post(
        TICKET_URL,
        json={"content_type": "video/mp4", "size_bytes": 1024, "duration_s": 3.0},
        headers=auth_headers(),
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error_code"] == "storage_unavailable"
    assert body["retryable"] is True
