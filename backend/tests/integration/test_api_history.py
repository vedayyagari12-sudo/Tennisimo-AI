"""DATABASE_SETUP.md Part 3.4 — ``GET /v1/analyses``, flat rows + keyset paging."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from starlette.testclient import TestClient

from tests.integration.conftest import (
    OTHER_USER_ID,
    FakeRepository,
    auth_headers,
)

URL = "/v1/analyses"
ROW_KEYS = {"analysis_id", "created_at", "shot_type", "overall_score", "ball_speed_mph"}


def _seed(repo: FakeRepository, count: int, *, user_id: UUID | None = None) -> list[UUID]:
    base = datetime(2026, 9, 14, 9, 0, 0, tzinfo=UTC)
    ids: list[UUID] = []
    for index in range(count):
        analysis_id = uuid4()
        repo.seed_analysis(
            analysis_id=analysis_id,
            created_at=base + timedelta(minutes=index),
            user_id=user_id if user_id is not None else repo_default_user(),
        )
        ids.append(analysis_id)
    return ids


def repo_default_user() -> UUID:
    from tests.integration.conftest import USER_ID

    return USER_ID


def test_empty_history_is_an_empty_page(client: TestClient) -> None:
    response = client.get(URL, headers=auth_headers())
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_rows_are_flat_five_keys_newest_first(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    _seed(fake_repository, 3)
    body = client.get(URL, headers=auth_headers()).json()
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert set(item) == ROW_KEYS
    stamps = [item["created_at"] for item in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert body["next_cursor"] is None


def test_nulls_are_preserved_and_never_become_zero(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    fake_repository.seed_analysis(
        analysis_id=uuid4(),
        created_at=datetime(2026, 9, 13, 17, 2, 8, 900000, tzinfo=UTC),
        overall_score=None,
        ball_speed_mph=None,
        shot_type="backhand_two_handed",
    )
    item = client.get(URL, headers=auth_headers()).json()["items"][0]
    assert item["overall_score"] is None
    assert item["ball_speed_mph"] is None


def test_zero_score_is_distinguishable_from_unavailable(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    fake_repository.seed_analysis(
        analysis_id=uuid4(),
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        overall_score=0.0,
    )
    item = client.get(URL, headers=auth_headers()).json()["items"][0]
    assert item["overall_score"] == 0.0
    assert item["overall_score"] is not None


def test_keyset_cursor_pages_across_two_pages_without_overlap(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    _seed(fake_repository, 5)

    first = client.get(URL, params={"limit": 2}, headers=auth_headers()).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]

    second = client.get(
        URL, params={"limit": 2, "cursor": first["next_cursor"]}, headers=auth_headers()
    ).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"]

    third = client.get(
        URL, params={"limit": 2, "cursor": second["next_cursor"]}, headers=auth_headers()
    ).json()
    assert len(third["items"]) == 1
    # Last page: no extra row came back, so no cursor.
    assert third["next_cursor"] is None

    seen = [i["analysis_id"] for page in (first, second, third) for i in page["items"]]
    assert len(seen) == len(set(seen)) == 5


def test_history_is_scoped_to_the_caller(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    _seed(fake_repository, 2)
    _seed(fake_repository, 3, user_id=OTHER_USER_ID)
    body = client.get(URL, headers=auth_headers()).json()
    assert len(body["items"]) == 2


def test_limit_out_of_range_is_invalid_request(client: TestClient) -> None:
    for limit in (0, 101):
        response = client.get(URL, params={"limit": limit}, headers=auth_headers())
        assert response.status_code == 400
        assert response.json()["error_code"] == "invalid_request"
        assert "detail" not in response.json()


def test_undecodable_cursor_is_invalid_request(client: TestClient) -> None:
    response = client.get(URL, params={"cursor": "!!!not-base64!!!"}, headers=auth_headers())
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_history_requires_auth(client: TestClient) -> None:
    response = client.get(URL)
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth_invalid_token"
