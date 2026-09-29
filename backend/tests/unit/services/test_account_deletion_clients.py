"""Wire-level tests for the three HTTP calls behind `DELETE /v1/account`.

Driven through `httpx.MockTransport`: no network, no real Supabase. Each test
asserts the exact request shape the real service expects:

* Storage list  -- ``POST /storage/v1/object/list/{bucket}`` ``{prefix, limit, offset, sortBy}``
* Storage delete -- ``DELETE /storage/v1/object/{bucket}`` ``{"prefixes": [full keys]}``
* PostgREST     -- ``DELETE /rest/v1/analyses?user_id=eq.X`` then ``/analysis_jobs``
* GoTrue        -- ``DELETE /auth/v1/admin/users/{id}`` ``{"should_soft_delete": false}``
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.api.errors import ApiError
from app.config import Settings
from app.models.enums import ErrorCode
from app.services.auth_admin import SupabaseAuthAdminClient
from app.services.repository import SupabaseRepository
from app.services.storage import STORAGE_BATCH_SIZE, SupabaseStorageClient

FAKE_ENV = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}
BASE = FAKE_ENV["SUPABASE_URL"]
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
FOLDER = f"swing-videos/{USER_ID}"

Handler = Callable[[httpx.Request], httpx.Response]


def _body(request: httpx.Request) -> Any:
    return json.loads(request.content) if request.content else None


def _run(handler: Handler, action: Callable[[httpx.AsyncClient], Any]) -> Any:
    async def go() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await action(client)

    return asyncio.run(go())


def _storage_delete(handler: Handler, folder: str = FOLDER) -> int:
    return _run(handler, lambda c: SupabaseStorageClient(Settings(env=FAKE_ENV), c).delete_folder(folder))


def _file(name: str) -> dict[str, Any]:
    return {"name": name, "id": f"id-{name}", "metadata": {"size": 1}}


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


class FakeStorageServer:
    """Folder -> entries, answering list/delete like Supabase Storage does."""

    def __init__(self, tree: dict[str, list[dict[str, Any]]]) -> None:
        self.tree = tree
        self.requests: list[httpx.Request] = []
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = _body(request)
        if request.method == "POST" and request.url.path == "/storage/v1/object/list/swing-videos":
            entries = self.tree.get(body["prefix"], [])
            page = entries[body["offset"] : body["offset"] + body["limit"]]
            return httpx.Response(200, json=page)
        if request.method == "DELETE" and request.url.path == "/storage/v1/object/swing-videos":
            assert body["prefixes"], "Storage rejects an empty prefixes list (minItems: 1)"
            self.deleted.extend(body["prefixes"])
            return httpx.Response(200, json=[{"name": n} for n in body["prefixes"]])
        return httpx.Response(418)

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method]


def test_storage_lists_callers_folder_then_bulk_deletes_full_keys() -> None:
    server = FakeStorageServer({str(USER_ID): [_file("a.mp4"), _file("b.webm")]})

    removed = _storage_delete(server)

    assert removed == 2
    [listing] = server.calls("POST")
    assert _body(listing)["prefix"] == str(USER_ID)
    assert _body(listing)["offset"] == 0
    assert server.deleted == [f"{USER_ID}/a.mp4", f"{USER_ID}/b.webm"]
    for request in server.requests:
        assert request.headers["apikey"] == FAKE_ENV["SUPABASE_SERVICE_ROLE_KEY"]
        assert request.headers["authorization"] == f"Bearer {FAKE_ENV['SUPABASE_SERVICE_ROLE_KEY']}"


def test_storage_never_touches_another_users_folder() -> None:
    server = FakeStorageServer(
        {
            str(USER_ID): [_file("mine.mp4")],
            str(OTHER_USER_ID): [_file("theirs.mp4"), _file(f"{USER_ID}.mp4")],
        }
    )

    _storage_delete(server)

    assert [_body(r)["prefix"] for r in server.calls("POST")] == [str(USER_ID)]
    assert server.deleted == [f"{USER_ID}/mine.mp4"]
    assert all(key.startswith(f"{USER_ID}/") for key in server.deleted)


def test_storage_empty_folder_sends_no_delete_request() -> None:
    server = FakeStorageServer({})

    assert _storage_delete(server) == 0
    assert server.calls("DELETE") == []


def test_storage_paginates_listing_and_batches_deletes() -> None:
    total = STORAGE_BATCH_SIZE * 2 + 5
    server = FakeStorageServer({str(USER_ID): [_file(f"{i:04d}.mp4") for i in range(total)]})

    assert _storage_delete(server) == total

    assert [_body(r)["offset"] for r in server.calls("POST")] == [0, 100, 200]
    batches = [_body(r)["prefixes"] for r in server.calls("DELETE")]
    assert [len(b) for b in batches] == [100, 100, 5]
    assert len(set(server.deleted)) == total


def test_storage_descends_into_sub_folders() -> None:
    server = FakeStorageServer(
        {
            str(USER_ID): [{"name": "nested", "id": None}, _file("top.mp4")],
            f"{USER_ID}/nested": [_file("deep.mp4")],
        }
    )

    _storage_delete(server)

    assert sorted(server.deleted) == [f"{USER_ID}/nested/deep.mp4", f"{USER_ID}/top.mp4"]


@pytest.mark.parametrize("folder", ["swing-videos", "swing-videos/", "", "/x"])
def test_storage_refuses_a_bucket_wide_delete(folder: str) -> None:
    server = FakeStorageServer({})

    with pytest.raises(ValueError):
        _storage_delete(server, folder)
    assert server.requests == []


@pytest.mark.parametrize("failing_method", ["POST", "DELETE"])
def test_storage_error_is_retryable_storage_unavailable(failing_method: str) -> None:
    inner = FakeStorageServer({str(USER_ID): [_file("a.mp4")]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == failing_method:
            return httpx.Response(500)
        return inner(request)

    with pytest.raises(ApiError) as caught:
        _storage_delete(handler)
    assert caught.value.error_code is ErrorCode.STORAGE_UNAVAILABLE
    assert caught.value.retryable is True


# --------------------------------------------------------------------------- #
# PostgREST rows
# --------------------------------------------------------------------------- #


def _repo_delete(handler: Handler) -> None:
    _run(handler, lambda c: SupabaseRepository(Settings(env=FAKE_ENV), c).delete_user_rows(USER_ID))


def test_rows_deleted_analyses_first_then_jobs_filtered_to_caller() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    _repo_delete(handler)

    assert [(r.method, r.url.path) for r in seen] == [
        ("DELETE", "/rest/v1/analyses"),
        ("DELETE", "/rest/v1/analysis_jobs"),
    ]
    for request in seen:
        assert request.url.params["user_id"] == f"eq.{USER_ID}"
        assert list(request.url.params.keys()) == ["user_id"]


def test_rows_zero_matching_is_not_an_error() -> None:
    # PostgREST answers a filtered DELETE that matches nothing with 204.
    _repo_delete(lambda request: httpx.Response(204))


def test_rows_postgrest_failure_is_retryable_internal_error() -> None:
    with pytest.raises(ApiError) as caught:
        _repo_delete(lambda request: httpx.Response(503))
    assert caught.value.error_code is ErrorCode.INTERNAL_ERROR
    assert caught.value.retryable is True


# --------------------------------------------------------------------------- #
# GoTrue admin
# --------------------------------------------------------------------------- #


def _auth_delete(handler: Handler) -> None:
    _run(handler, lambda c: SupabaseAuthAdminClient(Settings(env=FAKE_ENV), c).delete_user(USER_ID))


def test_auth_delete_is_a_hard_delete_to_the_admin_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    _auth_delete(handler)

    [request] = seen
    assert request.method == "DELETE"
    assert str(request.url) == f"{BASE}/auth/v1/admin/users/{USER_ID}"
    assert _body(request) == {"should_soft_delete": False}
    key = FAKE_ENV["SUPABASE_SERVICE_ROLE_KEY"]
    assert request.headers["apikey"] == key
    assert request.headers["authorization"] == f"Bearer {key}"


def test_auth_user_already_gone_is_success() -> None:
    _auth_delete(
        lambda request: httpx.Response(
            404, json={"code": 404, "error_code": "user_not_found", "msg": "User not found"}
        )
    )


@pytest.mark.parametrize("status", [401, 403, 500])
def test_auth_other_failures_are_retryable_internal_error(status: int) -> None:
    with pytest.raises(ApiError) as caught:
        _auth_delete(lambda request: httpx.Response(status))
    assert caught.value.error_code is ErrorCode.INTERNAL_ERROR
    assert caught.value.retryable is True


def test_auth_transport_error_is_retryable_internal_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ApiError) as caught:
        _auth_delete(handler)
    assert caught.value.error_code is ErrorCode.INTERNAL_ERROR
