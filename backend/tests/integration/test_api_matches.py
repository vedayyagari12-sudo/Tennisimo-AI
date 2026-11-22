"""``/v1/matches`` over HTTP, with an in-memory store and a fake analyser."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.models.requests import MATCH_MAX_UPLOAD_BYTES, MATCH_PART_BYTES
from app.services.match_jobs import MatchJobs, get_match_jobs
from tests.integration.conftest import OTHER_TOKEN, USER_ID, auth_headers
from tests.unit.services.test_match_jobs import MemoryStore, fake_feedback, ok_analyzer

BUCKET = "swing-videos"


@pytest.fixture()
def match_client(client: TestClient) -> Iterator[tuple[TestClient, MemoryStore]]:
    store = MemoryStore()
    holder: dict[str, MatchJobs] = {}

    async def jobs() -> MatchJobs:
        if "jobs" not in holder:
            holder["jobs"] = MatchJobs(
                store=store,  # type: ignore[arg-type]
                settings=client.app.state.settings,  # type: ignore[attr-defined]
                loop=asyncio.get_running_loop(),
                analyze=ok_analyzer,
                coach=fake_feedback,  # no network call in tests
            )
        return holder["jobs"]

    client.app.dependency_overrides[get_match_jobs] = jobs  # type: ignore[attr-defined]
    yield client, store
    # Let any match still running finish while the app loop is alive.
    if "jobs" in holder:
        deadline = time.monotonic() + 10.0
        while not holder["jobs"].idle() and time.monotonic() < deadline:
            time.sleep(0.02)
        holder["jobs"].shutdown()


def upload(store: MemoryStore) -> str:
    path = f"{BUCKET}/{USER_ID}/{uuid4()}.mp4"
    store.objects[path] = b"video"
    return path


def wait_for(client: TestClient, match_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        body = client.get(f"/v1/matches/{match_id}", headers=auth_headers()).json()
        if body["status"] == status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"match never reached {status}")


def test_create_poll_list_and_delete(match_client: tuple[TestClient, MemoryStore]) -> None:
    client, store = match_client
    path = upload(store)
    created = client.post(
        "/v1/matches", json={"storage_path": path, "court_format": "doubles"}, headers=auth_headers()
    )
    assert created.status_code == 202, created.text
    match_id = created.json()["match_id"]
    assert created.json()["status"] == "queued"

    done = wait_for(client, match_id, "complete")
    assert done["court_format"] == "doubles"
    assert done["result"]["court_engine_version"]
    assert done["stats"]["rallies"] >= 0

    picture = client.get(f"/v1/matches/{match_id}/court.jpg", headers=auth_headers())
    assert picture.status_code == 200
    assert picture.headers["content-type"] == "image/jpeg"

    listed = client.get("/v1/matches", headers=auth_headers())
    assert listed.status_code == 200
    assert [m["match_id"] for m in listed.json()["items"]] == [match_id]
    assert "result" not in listed.json()["items"][0]

    assert client.delete(f"/v1/matches/{match_id}", headers=auth_headers()).status_code == 204
    assert client.get(f"/v1/matches/{match_id}", headers=auth_headers()).status_code == 404


def test_a_rally_has_feedback_and_lists_apart_from_matches(
    match_client: tuple[TestClient, MemoryStore],
) -> None:
    client, store = match_client
    rally = client.post(
        "/v1/matches", json={"storage_path": upload(store), "kind": "rally"}, headers=auth_headers()
    )
    assert rally.status_code == 202, rally.text
    rally_id = rally.json()["match_id"]
    done = wait_for(client, rally_id, "complete")
    assert done["kind"] == "rally"
    assert done["feedback"]["summary"] and done["feedback"]["source"] == "gemini"

    match = client.post("/v1/matches", json={"storage_path": upload(store)}, headers=auth_headers())
    match_id = match.json()["match_id"]
    assert wait_for(client, match_id, "complete")["feedback"] is None

    def listed(query: str) -> list[str]:
        response = client.get(f"/v1/matches{query}", headers=auth_headers())
        assert response.status_code == 200, response.text
        return [m["match_id"] for m in response.json()["items"]]

    assert listed("?kind=rally") == [rally_id]
    assert listed("?kind=match") == [match_id]
    assert sorted(listed("")) == sorted([rally_id, match_id])
    assert client.get("/v1/matches?kind=serve", headers=auth_headers()).status_code == 400


def test_another_users_match_is_not_found(match_client: tuple[TestClient, MemoryStore]) -> None:
    client, store = match_client
    created = client.post("/v1/matches", json={"storage_path": upload(store)}, headers=auth_headers())
    match_id = created.json()["match_id"]
    other = client.get(f"/v1/matches/{match_id}", headers=auth_headers(OTHER_TOKEN))
    assert other.status_code == 404
    assert other.json()["error_code"] == "analysis_not_found"


def test_another_users_video_is_forbidden(match_client: tuple[TestClient, MemoryStore]) -> None:
    client, _store = match_client
    response = client.post(
        "/v1/matches",
        json={"storage_path": f"{BUCKET}/{uuid4()}/{uuid4()}.mp4"},
        headers=auth_headers(),
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "storage_path_forbidden"


def test_unknown_fields_are_invalid(match_client: tuple[TestClient, MemoryStore]) -> None:
    client, store = match_client
    response = client.post(
        "/v1/matches", json={"storage_path": upload(store), "surprise": 1}, headers=auth_headers()
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_matches_need_a_signed_in_user(match_client: tuple[TestClient, MemoryStore]) -> None:
    client, _store = match_client
    assert client.get("/v1/matches").status_code == 401


def test_a_match_upload_ticket_allows_five_minutes(client: TestClient) -> None:
    body = {"content_type": "video/mp4", "size_bytes": 1000, "duration_s": 300.0}
    swing = client.post("/v1/uploads/ticket", json=body, headers=auth_headers())
    assert swing.status_code == 400
    match = client.post("/v1/uploads/ticket", json={**body, "purpose": "match"}, headers=auth_headers())
    assert match.status_code == 200, match.text
    too_long = client.post(
        "/v1/uploads/ticket", json={**body, "duration_s": 301.0, "purpose": "match"}, headers=auth_headers()
    )
    assert too_long.status_code == 400


def test_a_large_match_is_uploaded_in_pieces_under_the_per_file_limit(client: TestClient) -> None:
    size = 2 * MATCH_PART_BYTES + 5
    response = client.post(
        "/v1/uploads/ticket",
        json={"content_type": "video/mp4", "size_bytes": size, "duration_s": 280.0, "purpose": "match"},
        headers=auth_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["part_bytes"] == MATCH_PART_BYTES < 50 * 1024 * 1024
    assert [p["storage_path"] for p in body["parts"]] == [
        f"{body['storage_path']}.part000", f"{body['storage_path']}.part001", f"{body['storage_path']}.part002",
    ]
    swing = client.post(
        "/v1/uploads/ticket",
        json={"content_type": "video/mp4", "size_bytes": 1000, "duration_s": 10.0},
        headers=auth_headers(),
    )
    assert swing.json()["parts"] == [] and swing.json()["part_bytes"] is None


def test_a_match_over_the_total_cap_is_too_large(client: TestClient) -> None:
    response = client.post(
        "/v1/uploads/ticket",
        json={"content_type": "video/mp4", "size_bytes": MATCH_MAX_UPLOAD_BYTES + 1, "duration_s": 280.0,
              "purpose": "match"},
        headers=auth_headers(),
    )
    assert response.status_code == 413
