"""The background job runner and the Part 3.5 state machine.

Four states, three legal transitions, no others::

    queued ──> running ──> succeeded
                 │
                 └───────> failed

`succeeded` and `failed` are terminal. A job never returns to `queued`. Retry is
a NEW `POST /v1/analyses` producing a NEW `analysis_id`; there is no retry
endpoint and no in-place restart, because a restart would make `analysis_id` stop
identifying one execution.

`ThreadPoolExecutor(max_workers=1)` per PIPELINE.md §0: exactly one clip is
analyzed at a time, and `--max-instances=1` is what keeps the in-process
queue-depth counter correct. Raising either without the other is a correctness
bug, not a throughput tweak.

The default runner is `default_job_runner`, which delegates to the real Stage
4-18 pipeline in `app.services.pipeline` (build-order step 9). The old
`unimplemented_job_runner` skeleton is kept below because it is the cheapest way
for a test to exercise the `running -> failed` transition without decoding a
video; it is no longer the default.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol
from uuid import UUID

from fastapi import Request

from app.api.errors import ApiError, default_message_for
from app.config import Settings
from app.models.enums import ErrorCode, JobStatus
from app.models.requests import CreateAnalysisRequest
from app.services.repository import JobRow, Repository, utcnow
from app.services.storage import StorageClient

logger = logging.getLogger("tennisform.orchestrator")

#: Stage reported when the process that knew the stage is gone. Honest, and NOT
#: a fabrication: it is not backfilled with a guess (Part 3.5).
UNKNOWN_STAGE: Final[str] = "unknown"


class JobFailure(Exception):
    """Raised by a job runner to move `running -> failed` with a specific code."""

    def __init__(self, error_code: ErrorCode, message: str | None = None, *, stage: str) -> None:
        self.error_code: ErrorCode = error_code
        self.message: str = message if message is not None else default_message_for(error_code)
        self.stage: str = stage
        super().__init__(f"{error_code.value} at {stage}: {self.message}")


@dataclass(frozen=True)
class JobContext:
    """Everything a job runner is given.

    The `request` is held in process memory rather than in the database because
    `analysis_jobs` has no calibration column (Part 1) -- consistent with the
    in-process executor, and lost on restart exactly like the job itself, which
    the staleness rule already covers.
    """

    job_id: UUID
    user_id: UUID
    storage_path: str
    request: CreateAnalysisRequest
    repository: Repository
    storage: StorageClient
    settings: Settings
    heartbeat: Callable[[], None]
    #: Run one async repository/storage call from the executor thread and wait.
    #: The runner is synchronous and CPU-bound and has no loop of its own, so
    #: every coroutine it needs -- the Stage 4 download, the Stage 18 write --
    #: is marshalled onto the application loop through this.
    run_async: Callable[[Coroutine[Any, Any, Any]], Any]


class JobRunner(Protocol):
    """Runs one job to a terminal state.

    Called on the executor thread. On success it MUST have persisted the
    `analyses` row and flipped the job to `succeeded` (via
    `repository.persist_success`). To fail, raise `JobFailure`.
    """

    def __call__(self, context: JobContext) -> None: ...


def unimplemented_job_runner(context: JobContext) -> None:
    """The Part 5.3 step-7 skeleton: fail immediately with `internal_error`.

    NO LONGER THE DEFAULT -- `default_job_runner` is. Retained as a test double
    for the failure transition, and so a deployment can fall back to it by
    passing `runner=` explicitly.
    """
    raise JobFailure(
        ErrorCode.INTERNAL_ERROR,
        "Analysis is not available yet.",
        stage="pipeline_not_wired",
    )


def default_job_runner(context: JobContext) -> None:
    """The real Stage 4-18 pipeline, imported lazily.

    ``app.services.pipeline`` pulls in MediaPipe, PyAV and OpenCV. Importing it
    at module scope would make every import of this module -- which the HTTP
    routes do -- pay for the pose model's dependency tree, so the import happens
    on the executor thread, on the first job, where the cost is already being
    paid.
    """
    from app.services.pipeline import run_analysis_job

    run_analysis_job(context)


def is_stale(job: JobRow, *, now: datetime, stale_after_s: int) -> bool:
    """PURE. The Part 3.5 staleness rule, evaluated on the READ path.

    A `running` job whose `heartbeat_at` is older than `job_heartbeat_stale_s`
    is dead: its worker was killed by an instance restart, a deploy, or a scale
    event, and nothing will ever update its row again.

    `heartbeat_at IS NULL` is NOT stale -- a queued job has no worker to beat,
    and a job that has just been marked running may not have beaten yet.

    Read-path evaluation rather than a background sweeper is the MVP choice:
    with `--max-instances=1` and a dev tier that scales to zero, a sweeper may
    not be running when a job goes stale, and two sweepers could race during a
    scale event. The read path is always running when it matters, because
    something is polling.
    """
    if job.status is not JobStatus.RUNNING or job.heartbeat_at is None:
        return False
    return job.heartbeat_at < now - timedelta(seconds=stale_after_s)


class JobOrchestrator:
    """Owns the executor, the in-process queue-depth counter, and the transitions."""

    def __init__(
        self,
        *,
        repository: Repository,
        storage: StorageClient,
        settings: Settings,
        loop: asyncio.AbstractEventLoop,
        runner: JobRunner = default_job_runner,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._settings = settings
        self._loop = loop
        self._runner = runner
        # max_workers=1 is load-bearing, not a default. See the module docstring.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._depth_lock = threading.Lock()
        self._depth = 0

    # -- admission control -------------------------------------------------

    @property
    def queue_depth(self) -> int:
        """Jobs currently queued or running in THIS process.

        Part 3.5's known limitation, restated so it is not rediscovered: this
        counter lives in process memory and is correct only under
        `--max-instances=1`. On the dev tier (`--min-instances=0`) a
        scale-from-zero event can briefly overlap two instances, in which case
        the cap admits up to 2x depth. Benign at zero real users; the fix is a
        DB-backed admission check and is deliberately not built for the MVP.
        """
        with self._depth_lock:
            return self._depth

    def has_capacity(self) -> bool:
        return self.queue_depth < self._settings.job_queue_max_depth

    # -- submission --------------------------------------------------------

    def submit(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
        storage_path: str,
        request: CreateAnalysisRequest,
    ) -> Future[None]:
        """Hand a already-inserted `queued` row to the executor.

        Returns the future so a caller (and a test) can wait deterministically.
        The route ignores it: the client learns the outcome by polling.
        """
        with self._depth_lock:
            self._depth += 1
        try:
            return self._executor.submit(
                self._run, job_id, user_id, storage_path, request
            )
        except Exception:  # pragma: no cover - executor already shut down
            with self._depth_lock:
                self._depth -= 1
            raise

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- the state machine -------------------------------------------------

    def _run(
        self,
        job_id: UUID,
        user_id: UUID,
        storage_path: str,
        request: CreateAnalysisRequest,
    ) -> None:
        try:
            self._await(self._repository.mark_running(job_id, utcnow()))
            context = JobContext(
                job_id=job_id,
                user_id=user_id,
                storage_path=storage_path,
                request=request,
                repository=self._repository,
                storage=self._storage,
                settings=self._settings,
                heartbeat=lambda: self._await(self._repository.heartbeat(job_id, utcnow())),
                run_async=self._await,
            )
            self._runner(context)
        except JobFailure as failure:
            self._fail(job_id, failure.error_code, failure.message, failure.stage)
        except ApiError as api_error:
            # A storage/DB error surfacing out of a runner is a job failure, not
            # an HTTP response: there is no request left to answer.
            self._fail(job_id, api_error.error_code, api_error.message, UNKNOWN_STAGE)
        except Exception:  # noqa: BLE001 - a dead job must still leave a row
            logger.exception("job %s crashed", job_id)
            self._fail(
                job_id,
                ErrorCode.INTERNAL_ERROR,
                default_message_for(ErrorCode.INTERNAL_ERROR),
                UNKNOWN_STAGE,
            )
        finally:
            with self._depth_lock:
                self._depth = max(0, self._depth - 1)

    def _fail(self, job_id: UUID, code: ErrorCode, message: str, stage: str) -> None:
        try:
            self._await(self._repository.mark_failed(job_id, code, message, stage))
        except Exception:  # noqa: BLE001 - best effort; the staleness rule is the net
            logger.exception("could not mark job %s failed", job_id)

    def _await(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run an async repository call from the executor thread.

        The repository is async (httpx.AsyncClient) and the executor thread has
        no loop of its own, so the coroutine is marshalled onto the application
        loop and waited on. CPU-bound work stays on this thread; only the I/O
        crosses over.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


async def mark_worker_lost(repository: Repository, job_id: UUID) -> None:
    """Best-effort write-back of the staleness verdict (Part 3.5).

    History and metrics should be right, but the poll response must NOT depend
    on this write succeeding -- so every failure here is swallowed and logged.
    """
    try:
        await repository.mark_failed(
            job_id,
            ErrorCode.WORKER_LOST,
            default_message_for(ErrorCode.WORKER_LOST),
            UNKNOWN_STAGE,
        )
    except Exception:  # noqa: BLE001
        logger.warning("best-effort worker_lost write-back failed for job %s", job_id)


def get_orchestrator(request: Request) -> JobOrchestrator:
    """Dependency returning the startup-built orchestrator from ``app.state``."""
    orchestrator: JobOrchestrator | None = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:  # pragma: no cover - misconfigured app
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    return orchestrator
