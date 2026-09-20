"""Stage 3 — ``POST /v1/analyses``: the synchronous validation ladder."""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from app.api.errors import ApiError
from app.config import MAX_UPLOAD_BYTES
from app.models.enums import ErrorCode
from app.services.storage import ObjectHead
from tests.integration.conftest import (
    FakeOrchestrator,
    FakeRepository,
    FakeStorageClient,
    auth_headers,
    storage_path_for,
)

URL = "/v1/analyses"


def _calibration(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "point_a": {"x": 0.182, "y": 0.744},
        "point_b": {"x": 0.617, "y": 0.488},
        "reference": "sideline_baseline_to_net",
        "distance_m": 11.885,
        "capture_width_px": 1080,
        "capture_height_px": 1920,
        "capture_rotation_deg": 0,
    }
    body.update(overrides)
    return body


def test_accepted_job_returns_202_queued(
    client: TestClient, fake_repository: FakeRepository, fake_orchestrator: FakeOrchestrator
) -> None:
    response = client.post(
        URL,
        json={
            "storage_path": storage_path_for(),
            "handedness_hint": "right",
            "camera_view_hint": "side_on",
            "label_hint": "forehand_topspin",
        },
        headers=auth_headers(),
    )
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "analysis_id",
        "status",
        "poll_url",
        "estimated_seconds",
        "ball_speed_requested",
    }
    assert body["status"] == "queued"
    assert body["poll_url"] == f"/v1/analyses/{body['analysis_id']}"
    assert body["estimated_seconds"] == 25
    assert body["ball_speed_requested"] is False
    assert len(fake_repository.jobs) == 1
    assert len(fake_orchestrator.submitted) == 1


def test_missing_calibration_is_not_an_error(client: TestClient) -> None:
    """Absence of ball_speed_calibration never blocks anything."""
    response = client.post(
        URL, json={"storage_path": storage_path_for()}, headers=auth_headers()
    )
    assert response.status_code == 202
    assert response.json()["ball_speed_requested"] is False


def test_valid_calibration_is_echoed_as_requested(client: TestClient) -> None:
    response = client.post(
        URL,
        json={"storage_path": storage_path_for(), "ball_speed_calibration": _calibration()},
        headers=auth_headers(),
    )
    assert response.status_code == 202
    assert response.json()["ball_speed_requested"] is True


def test_calibration_distance_mismatch_is_calibration_invalid(client: TestClient) -> None:
    """A desynced client build must be reported, never silently overridden."""
    response = client.post(
        URL,
        json={
            "storage_path": storage_path_for(),
            "ball_speed_calibration": _calibration(distance_m=11.0),
        },
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "calibration_invalid"


def test_calibration_points_too_close_is_calibration_invalid(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "storage_path": storage_path_for(),
            "ball_speed_calibration": _calibration(
                point_a={"x": 0.50, "y": 0.50}, point_b={"x": 0.51, "y": 0.51}
            ),
        },
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "calibration_invalid"


def test_object_not_found_is_404(client: TestClient, fake_storage: FakeStorageClient) -> None:
    fake_storage.head_error = ApiError(ErrorCode.OBJECT_NOT_FOUND)
    response = client.post(
        URL, json={"storage_path": storage_path_for()}, headers=auth_headers()
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "object_not_found"


def test_actual_object_over_the_cap_is_413(
    client: TestClient, fake_storage: FakeStorageClient
) -> None:
    fake_storage.head_result = ObjectHead(
        size_bytes=MAX_UPLOAD_BYTES + 1, content_type="video/mp4"
    )
    response = client.post(
        URL, json={"storage_path": storage_path_for()}, headers=auth_headers()
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "file_too_large"


def test_zero_byte_object_is_not_treated_as_unknown_size(
    client: TestClient, fake_storage: FakeStorageClient
) -> None:
    """0 is a measured size. It must not be confused with "no size header"."""
    fake_storage.head_result = ObjectHead(size_bytes=0, content_type="video/mp4")
    response = client.post(
        URL, json={"storage_path": storage_path_for()}, headers=auth_headers()
    )
    assert response.status_code == 202


def test_queue_depth_four_is_429_with_retry_after(
    client: TestClient, fake_orchestrator: FakeOrchestrator, fake_repository: FakeRepository
) -> None:
    fake_orchestrator.depth = 4
    response = client.post(
        URL, json={"storage_path": storage_path_for()}, headers=auth_headers()
    )
    assert response.status_code == 429
    body = response.json()
    assert body["error_code"] == "queue_full"
    assert body["retryable"] is True
    assert response.headers["Retry-After"] == "30"
    # Rejected at admission: no row was written and nothing was submitted.
    assert fake_repository.jobs == {}


def test_estimated_seconds_grows_with_queue_depth(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.depth = 2
    response = client.post(
        URL, json={"storage_path": storage_path_for()}, headers=auth_headers()
    )
    assert response.status_code == 202
    assert response.json()["estimated_seconds"] == 75


def test_unknown_field_is_invalid_request(client: TestClient) -> None:
    response = client.post(
        URL,
        json={"storage_path": storage_path_for(), "nope": True},
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"
