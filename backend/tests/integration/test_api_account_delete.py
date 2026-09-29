"""`DELETE /v1/account` through the full HTTP stack, with every boundary faked."""

from __future__ import annotations

from datetime import UTC, datetime

from starlette.testclient import TestClient

from app.api.errors import ApiError
from app.models.enums import ErrorCode
from tests.integration.conftest import (
    OTHER_USER_ID,
    USER_ID,
    FakeAuthAdminClient,
    FakeRepository,
    FakeStorageClient,
    auth_headers,
    storage_path_for,
)

ENVELOPE_KEYS = {"error_code", "message", "retryable", "request_id"}


def _seed_two_users(storage: FakeStorageClient, repository: FakeRepository) -> None:
    storage.objects = {
        storage_path_for(USER_ID, "a.mp4"),
        storage_path_for(USER_ID, "b.mp4"),
        storage_path_for(OTHER_USER_ID, "c.mp4"),
    }
    now = datetime.now(UTC)
    for owner in (USER_ID, OTHER_USER_ID):
        job = repository.seed_job(user_id=owner)
        repository.seed_analysis(analysis_id=job.id, created_at=now, user_id=owner, payload={})


def test_success_is_204_with_no_body_and_runs_storage_rows_auth_in_order(
    client: TestClient,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_auth_admin: FakeAuthAdminClient,
) -> None:
    shared: list[str] = []
    fake_storage.events = fake_repository.events = fake_auth_admin.events = shared
    _seed_two_users(fake_storage, fake_repository)

    response = client.delete("/v1/account", headers=auth_headers())

    assert response.status_code == 204
    assert response.content == b""
    assert shared == ["storage", "rows", "auth"]
    assert fake_auth_admin.delete_calls == [USER_ID]
    assert USER_ID not in fake_auth_admin.users
    assert all(job.user_id != USER_ID for job in fake_repository.jobs.values())
    assert all(owner != USER_ID for owner, _ in fake_repository.analyses)


def test_only_the_callers_own_prefix_rows_and_user_are_touched(
    client: TestClient,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_auth_admin: FakeAuthAdminClient,
) -> None:
    _seed_two_users(fake_storage, fake_repository)

    assert client.delete("/v1/account", headers=auth_headers()).status_code == 204

    assert fake_storage.delete_folder_calls == [f"swing-videos/{USER_ID}"]
    assert fake_storage.objects == {storage_path_for(OTHER_USER_ID, "c.mp4")}
    assert fake_repository.delete_rows_calls == [USER_ID]
    assert [job.user_id for job in fake_repository.jobs.values()] == [OTHER_USER_ID]
    assert [owner for owner, _ in fake_repository.analyses] == [OTHER_USER_ID]
    assert OTHER_USER_ID in fake_auth_admin.users


def test_missing_token_is_401_envelope_and_deletes_nothing(
    client: TestClient,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_auth_admin: FakeAuthAdminClient,
) -> None:
    response = client.delete("/v1/account")

    assert response.status_code == 401
    assert set(response.json()) == ENVELOPE_KEYS
    assert response.json()["error_code"] == "auth_invalid_token"
    assert fake_storage.delete_folder_calls == []
    assert fake_repository.delete_rows_calls == []
    assert fake_auth_admin.delete_calls == []


def test_invalid_token_is_401_and_deletes_nothing(
    client: TestClient, fake_auth_admin: FakeAuthAdminClient
) -> None:
    response = client.delete("/v1/account", headers=auth_headers("not.a.known.token"))

    assert response.status_code == 401
    assert fake_auth_admin.delete_calls == []


def test_repeat_call_with_everything_already_gone_is_still_204(
    client: TestClient,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_auth_admin: FakeAuthAdminClient,
) -> None:
    _seed_two_users(fake_storage, fake_repository)
    assert client.delete("/v1/account", headers=auth_headers()).status_code == 204

    # Same still-unexpired token, nothing left to delete: a clean no-op.
    assert client.delete("/v1/account", headers=auth_headers()).status_code == 204
    assert fake_auth_admin.delete_calls == [USER_ID, USER_ID]


def test_storage_failure_stops_before_rows_and_auth_then_retry_completes(
    client: TestClient,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_auth_admin: FakeAuthAdminClient,
) -> None:
    _seed_two_users(fake_storage, fake_repository)
    fake_storage.delete_folder_error = ApiError(ErrorCode.STORAGE_UNAVAILABLE)

    failed = client.delete("/v1/account", headers=auth_headers())

    assert failed.status_code == 503
    assert set(failed.json()) == ENVELOPE_KEYS
    assert failed.json()["retryable"] is True
    assert fake_repository.delete_rows_calls == []
    assert fake_auth_admin.delete_calls == []
    assert USER_ID in fake_auth_admin.users  # can still sign in and retry

    fake_storage.delete_folder_error = None
    assert client.delete("/v1/account", headers=auth_headers()).status_code == 204
    assert USER_ID not in fake_auth_admin.users


def test_auth_failure_after_data_deleted_is_retryable_and_retry_completes(
    client: TestClient,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_auth_admin: FakeAuthAdminClient,
) -> None:
    _seed_two_users(fake_storage, fake_repository)
    fake_auth_admin.delete_error = ApiError(ErrorCode.INTERNAL_ERROR)

    failed = client.delete("/v1/account", headers=auth_headers())

    assert failed.status_code == 500
    assert failed.json()["retryable"] is True
    assert USER_ID in fake_auth_admin.users

    fake_auth_admin.delete_error = None
    assert client.delete("/v1/account", headers=auth_headers()).status_code == 204
    assert USER_ID not in fake_auth_admin.users
    assert fake_storage.objects == {storage_path_for(OTHER_USER_ID, "c.mp4")}
