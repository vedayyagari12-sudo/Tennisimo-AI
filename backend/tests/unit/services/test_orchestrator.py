"""The Part 3.5 state machine and the staleness rule.

The staleness tests are pure. The state-machine tests drive a real
``ThreadPoolExecutor`` against a real event loop running on a background thread,
because the repository is async and the worker marshals its writes onto the
loop. No pytest-asyncio plugin is needed and none is installed.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config import Settings
from app.models.enums import ErrorCode, JobStatus
from app.models.requests import CreateAnalysisRequest
from app.services.orchestrator import (
    JobContext,
    JobFailure,
    JobOrchestrator,
    default_job_runner,
    is_stale,
    unimplemented_job_runner,
)
from app.services.repository import JobRow

FAKE_ENV = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}
USER_ID = UUID("11111111-1111-4111-8111-111111111111")


def make_job(**overrides: Any) -> JobRow:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "user_id": USER_ID,
        "storage_path": f"swing-videos/{USER_ID}/clip.mp4",
        "status": JobStatus.RUNNING,
        "created_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return JobRow(**fields)


# ---------------------------------------------------------------------------
# is_stale — PURE
# ---------------------------------------------------------------------------


def test_running_job_beyond_the_window_is_stale() -> None:
    now = datetime.now(UTC)
    job = make_job(heartbeat_at=now - timedelta(seconds=181))
    assert is_stale(job, now=now, stale_after_s=180) is True


def test_running_job_inside_the_window_is_not_stale() -> None:
    now = datetime.now(UTC)
    job = make_job(heartbeat_at=now - timedelta(seconds=179))
    assert is_stale(job, now=now, stale_after_s=180) is False


def test_running_job_without_a_heartbeat_is_not_stale() -> None:
    """A queued job has no worker to beat, and a just-started one has not beaten."""
    now = datetime.now(UTC)
    assert is_stale(make_job(heartbeat_at=None), now=now, stale_after_s=180) is False


@pytest.mark.parametrize(
    "status", [JobStatus.QUEUED, JobStatus.SUCCEEDED, JobStatus.FAILED]
)
def test_only_running_jobs_can_be_stale(status: JobStatus) -> None:
    now = datetime.now(UTC)
    job = make_job(status=status, heartbeat_at=now - timedelta(days=1))
    assert is_stale(job, now=now, stale_after_s=180) is False


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


class RecordingRepository:
    """Records transitions. Async, like the real one."""

    def __init__(self) -> None:
        self.transitions: list[tuple[str, Any]] = []

    async def mark_running(self, job_id: UUID, at: datetime) -> None:
        self.transitions.append(("running", job_id))

    async def heartbeat(self, job_id: UUID, at: datetime) -> None:
        self.transitions.append(("heartbeat", job_id))

    async def mark_failed(
        self, job_id: UUID, error_code: ErrorCode, message: str, stage: str
    ) -> None:
        self.transitions.append(("failed", (job_id, error_code, stage)))


@pytest.fixture()
def settings() -> Settings:
    return Settings(env=FAKE_ENV)


@pytest.fixture()
def loop():
    new_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=new_loop.run_forever, daemon=True)
    thread.start()
    yield new_loop
    new_loop.call_soon_threadsafe(new_loop.stop)
    thread.join(timeout=5)
    new_loop.close()


def make_request() -> CreateAnalysisRequest:
    return CreateAnalysisRequest(storage_path=f"swing-videos/{USER_ID}/clip.mp4")


def run_one(orchestrator: JobOrchestrator, job_id: UUID) -> None:
    future = orchestrator.submit(
        job_id=job_id,
        user_id=USER_ID,
        storage_path=f"swing-videos/{USER_ID}/clip.mp4",
        request=make_request(),
    )
    future.result(timeout=10)


def test_skeleton_runner_walks_queued_running_failed(
    settings: Settings, loop: asyncio.AbstractEventLoop
) -> None:
    """The Part 5.3 step-7 skeleton: enough to exercise the whole machine.

    Passed explicitly now that the DEFAULT runner is the real Stage 4-18
    pipeline, which would try to download a clip.
    """
    repository = RecordingRepository()
    orchestrator = JobOrchestrator(
        repository=repository,  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        settings=settings,
        loop=loop,
        runner=unimplemented_job_runner,
    )
    job_id = uuid4()
    try:
        run_one(orchestrator, job_id)
    finally:
        orchestrator.shutdown()

    kinds = [kind for kind, _ in repository.transitions]
    assert kinds == ["running", "failed"]
    _, (failed_id, code, stage) = repository.transitions[1]
    assert failed_id == job_id
    assert code is ErrorCode.INTERNAL_ERROR
    assert stage == "pipeline_not_wired"


def test_runner_raising_job_failure_records_its_code_and_stage(
    settings: Settings, loop: asyncio.AbstractEventLoop
) -> None:
    repository = RecordingRepository()

    def runner(context: JobContext) -> None:
        context.heartbeat()
        raise JobFailure(ErrorCode.CONTACT_NOT_FOUND, stage="contact_detection")

    orchestrator = JobOrchestrator(
        repository=repository,  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        settings=settings,
        loop=loop,
        runner=runner,
    )
    try:
        run_one(orchestrator, uuid4())
    finally:
        orchestrator.shutdown()

    assert [kind for kind, _ in repository.transitions] == ["running", "heartbeat", "failed"]
    _, (_, code, stage) = repository.transitions[-1]
    assert code is ErrorCode.CONTACT_NOT_FOUND
    assert stage == "contact_detection"


def test_an_unexpected_crash_still_leaves_a_failed_row(
    settings: Settings, loop: asyncio.AbstractEventLoop
) -> None:
    repository = RecordingRepository()

    def runner(context: JobContext) -> None:
        raise ZeroDivisionError("something nobody predicted")

    orchestrator = JobOrchestrator(
        repository=repository,  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        settings=settings,
        loop=loop,
        runner=runner,
    )
    try:
        run_one(orchestrator, uuid4())
    finally:
        orchestrator.shutdown()

    _, (_, code, stage) = repository.transitions[-1]
    assert code is ErrorCode.INTERNAL_ERROR
    assert stage == "unknown"


def test_queue_depth_is_released_after_the_job_finishes(
    settings: Settings, loop: asyncio.AbstractEventLoop
) -> None:
    repository = RecordingRepository()
    orchestrator = JobOrchestrator(
        repository=repository,  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        settings=settings,
        loop=loop,
    )
    try:
        assert orchestrator.queue_depth == 0
        assert orchestrator.has_capacity() is True
        run_one(orchestrator, uuid4())
        assert orchestrator.queue_depth == 0
    finally:
        orchestrator.shutdown()


def test_capacity_is_exhausted_at_the_configured_depth(
    settings: Settings, loop: asyncio.AbstractEventLoop
) -> None:
    """Depth 4 is job_queue_max_depth; the executor is max_workers=1."""
    repository = RecordingRepository()
    release = threading.Event()

    def blocking_runner(context: JobContext) -> None:
        release.wait(timeout=10)
        raise JobFailure(ErrorCode.INTERNAL_ERROR, stage="test")

    orchestrator = JobOrchestrator(
        repository=repository,  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        settings=settings,
        loop=loop,
        runner=blocking_runner,
    )
    futures = []
    try:
        for _ in range(settings.job_queue_max_depth):
            futures.append(
                orchestrator.submit(
                    job_id=uuid4(),
                    user_id=USER_ID,
                    storage_path=f"swing-videos/{USER_ID}/clip.mp4",
                    request=make_request(),
                )
            )
        assert orchestrator.queue_depth == 4
        assert orchestrator.has_capacity() is False
    finally:
        release.set()
        for future in futures:
            future.result(timeout=10)
        orchestrator.shutdown()


def test_default_runner_is_the_real_pipeline(
    settings: Settings, loop: asyncio.AbstractEventLoop
) -> None:
    """The orchestrator's default runner is the Stage 4-18 pipeline, not the skeleton."""
    import inspect

    orchestrator = JobOrchestrator(
        repository=RecordingRepository(),  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        settings=settings,
        loop=loop,
    )
    try:
        assert orchestrator._runner is default_job_runner
    finally:
        orchestrator.shutdown()
    assert (
        inspect.signature(JobOrchestrator.__init__).parameters["runner"].default
        is default_job_runner
    )


def test_unimplemented_runner_is_explicit_about_why() -> None:
    """Kept as a test double for the failure transition; no longer the default."""
    with pytest.raises(JobFailure) as excinfo:
        unimplemented_job_runner(None)  # type: ignore[arg-type]
    assert excinfo.value.error_code is ErrorCode.INTERNAL_ERROR
    assert excinfo.value.stage == "pipeline_not_wired"
