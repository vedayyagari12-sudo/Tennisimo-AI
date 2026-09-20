"""Stage 19 — ``GET /v1/analyses/{id}``, the polymorphic poll.

The REQUIRED INVARIANTS of DATABASE_SETUP.md Part 4.4 are asserted here, because
violating them does not raise, does not log and does not show an error: the
Flutter client's ``JobStatus.fromJson`` silently defaults an unknown status to
``queued``, so the symptom is a 120 s spinner on a job that finished.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from starlette.testclient import TestClient

from app.models.enums import ErrorCode, JobStatus
from tests.integration.conftest import (
    OTHER_TOKEN,
    USER_ID,
    FakeRepository,
    auth_headers,
)

JOB_KEYS = {"analysis_id", "status", "queue_position", "estimated_seconds_remaining", "error"}


def _finished_payload(analysis_id: Any, status: str = "complete") -> dict[str, Any]:
    """A minimal stored payload. Returned verbatim, so its exact keys matter."""
    return {
        "analysis_id": str(analysis_id),
        "user_id": str(USER_ID),
        "created_at": "2026-09-14T09:12:44.118000+00:00",
        "status": status,
        "pipeline_version": "v2",
        "scorecard": {"overall_score": 74.5, "categories": [], "metrics": []},
        "feedback": {"summary": "Nice shot.", "strengths": [], "improvements": []},
    }


def test_unknown_id_is_404_analysis_not_found(client: TestClient) -> None:
    response = client.get(f"/v1/analyses/{uuid4()}", headers=auth_headers())
    assert response.status_code == 404
    assert response.json()["error_code"] == "analysis_not_found"


def test_foreign_job_is_404_not_403(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    """The endpoint must not confirm that an id exists."""
    job = fake_repository.seed_job()
    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers(OTHER_TOKEN))
    assert response.status_code == 404
    assert response.json()["error_code"] == "analysis_not_found"


def test_queued_job_reports_position_and_eta(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    now = datetime.now(UTC)
    fake_repository.seed_job(created_at=now - timedelta(seconds=30))
    fake_repository.seed_job(created_at=now - timedelta(seconds=20))
    job = fake_repository.seed_job(created_at=now)

    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == JOB_KEYS
    assert body["status"] == "queued"
    assert body["queue_position"] == 3
    assert body["estimated_seconds_remaining"] == 75
    assert body["error"] is None


def test_running_job_has_null_queue_position_not_zero(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    now = datetime.now(UTC)
    job = fake_repository.seed_job(
        status=JobStatus.RUNNING, created_at=now, heartbeat_at=now
    )
    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    body = response.json()
    assert body["status"] == "running"
    # null, not 0: 0 would imply a measured position.
    assert body["queue_position"] is None
    assert "queue_position" in body  # present-and-null, not stripped
    assert body["estimated_seconds_remaining"] >= 1


def test_in_progress_body_carries_no_feedback_and_no_scorecard(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    """REQUIRED INVARIANT Part 4.4b: not even null-valued."""
    job = fake_repository.seed_job()
    body = client.get(f"/v1/analyses/{job.id}", headers=auth_headers()).json()
    assert "feedback" not in body
    assert "scorecard" not in body


def test_failed_job_is_http_200_with_a_nested_error(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    job = fake_repository.seed_job(
        status=JobStatus.FAILED,
        error_code=ErrorCode.CONTACT_NOT_FOUND,
        error_message="No clear ball-strike was found in the clip.",
        error_stage="contact_detection",
    )
    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    # The poll succeeded; the job did not.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["queue_position"] is None
    assert body["estimated_seconds_remaining"] is None
    # Nested key is `code`, NOT `error_code` (Part 4.1's deferred wart).
    assert set(body["error"]) == {"code", "message", "stage", "retryable"}
    assert body["error"]["code"] == "contact_not_found"
    assert body["error"]["stage"] == "contact_detection"
    assert body["error"]["retryable"] is False


def test_stale_heartbeat_reports_worker_lost(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    now = datetime.now(UTC)
    job = fake_repository.seed_job(
        status=JobStatus.RUNNING,
        created_at=now - timedelta(seconds=400),
        heartbeat_at=now - timedelta(seconds=181),  # older than job_heartbeat_stale_s
    )
    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "worker_lost"
    assert body["error"]["retryable"] is True
    # `unknown` is honest: the process that knew the stage is gone.
    assert body["error"]["stage"] == "unknown"
    # Best-effort write-back so history and metrics are right.
    assert fake_repository.failures[0][1] is ErrorCode.WORKER_LOST


def test_running_job_inside_the_window_is_not_stale(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    now = datetime.now(UTC)
    job = fake_repository.seed_job(
        status=JobStatus.RUNNING,
        created_at=now - timedelta(seconds=60),
        heartbeat_at=now - timedelta(seconds=179),
    )
    body = client.get(f"/v1/analyses/{job.id}", headers=auth_headers()).json()
    assert body["status"] == "running"


def test_running_job_without_a_heartbeat_is_not_stale(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    """A job just marked running has no heartbeat yet; that is not death."""
    job = fake_repository.seed_job(
        status=JobStatus.RUNNING,
        created_at=datetime.now(UTC) - timedelta(seconds=600),
        heartbeat_at=None,
    )
    body = client.get(f"/v1/analyses/{job.id}", headers=auth_headers()).json()
    assert body["status"] == "running"


def test_finished_job_returns_the_payload_verbatim_and_never_succeeded(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    """REQUIRED INVARIANT Part 4.4a."""
    job = fake_repository.seed_job(status=JobStatus.SUCCEEDED)
    payload = _finished_payload(job.id)
    fake_repository.seed_analysis(
        analysis_id=job.id, created_at=datetime.now(UTC), payload=payload
    )

    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body == payload
    assert body["status"] == "complete"
    assert body["status"] != "succeeded"
    # The client's discriminator: shape, not a type field.
    assert isinstance(body["feedback"], dict)
    assert isinstance(body["scorecard"], dict)


def test_partial_status_is_also_a_finished_body(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    job = fake_repository.seed_job(status=JobStatus.SUCCEEDED)
    payload = _finished_payload(job.id, status="partial")
    fake_repository.seed_analysis(
        analysis_id=job.id, created_at=datetime.now(UTC), payload=payload
    )
    body = client.get(f"/v1/analyses/{job.id}", headers=auth_headers()).json()
    assert body["status"] == "partial"


def test_succeeded_job_with_no_row_is_500_not_404(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    """The crash-between-writes case is a server fault, not a missing analysis."""
    job = fake_repository.seed_job(status=JobStatus.SUCCEEDED)
    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"


def test_stored_payload_with_a_job_status_is_refused(
    client: TestClient, fake_repository: FakeRepository
) -> None:
    """A payload whose status is `succeeded` would give the client an infinite
    spinner. It is refused rather than repaired -- repairing means inventing."""
    job = fake_repository.seed_job(status=JobStatus.SUCCEEDED)
    fake_repository.seed_analysis(
        analysis_id=job.id,
        created_at=datetime.now(UTC),
        payload=_finished_payload(job.id, status="succeeded"),
    )
    response = client.get(f"/v1/analyses/{job.id}", headers=auth_headers())
    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"


def test_poll_requires_auth(client: TestClient, fake_repository: FakeRepository) -> None:
    job = fake_repository.seed_job()
    response = client.get(f"/v1/analyses/{job.id}")
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth_invalid_token"
