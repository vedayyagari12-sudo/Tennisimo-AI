"""Stages 3 and 19 plus DATABASE_SETUP.md Part 3.4 — the `/v1/analyses` routes.

* `POST /v1/analyses`        -> 202, job accepted
* `GET  /v1/analyses/{id}`   -> 200, POLYMORPHIC (job status or finished analysis)
* `GET  /v1/analyses`        -> 200, flat history page with a keyset cursor
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.auth import AuthedUser, get_current_user
from app.api.errors import (
    REQUEST_ID_HEADER,
    ApiError,
    current_request_id,
    default_message_for,
    is_retryable,
    openapi_errors,
)
from app.config import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    JOB_ESTIMATED_SECONDS_BASE,
    QUEUE_FULL_RETRY_AFTER_S,
    Settings,
    get_settings,
)
from app.models.enums import AnalysisStatus, ErrorCode, JobStatus
from app.models.requests import CreateAnalysisRequest, CreateAnalysisResponse
from app.models.responses import (
    AnalysisError,
    AnalysisJobStatusResponse,
    AnalysisListItem,
    AnalysisListResponse,
    AnalysisResponse,
)
from app.services.orchestrator import (
    UNKNOWN_STAGE,
    JobOrchestrator,
    get_orchestrator,
    is_stale,
    mark_worker_lost,
)
from app.services.repository import (
    JobRow,
    Repository,
    decode_cursor,
    encode_cursor,
    get_repository,
    utcnow,
)
from app.services.storage import StorageClient, get_storage_client

logger = logging.getLogger("tennisform.analyses")

router = APIRouter(prefix="/v1/analyses", tags=["analyses"])


def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    """A JSON response carrying the correlation header explicitly."""
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={REQUEST_ID_HEADER: current_request_id()},
    )


# ---------------------------------------------------------------------------
# Stage 3 — POST /v1/analyses
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CreateAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=openapi_errors(
        ErrorCode.INVALID_REQUEST,
        ErrorCode.CALIBRATION_INVALID,
        ErrorCode.AUTH_INVALID_TOKEN,
        ErrorCode.STORAGE_PATH_FORBIDDEN,
        ErrorCode.OBJECT_NOT_FOUND,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.QUEUE_FULL,
        ErrorCode.STORAGE_UNAVAILABLE,
    ),
    summary="Queue an analysis of an already-uploaded clip.",
)
async def create_analysis(
    body: CreateAnalysisRequest,
    response: Response,
    user: AuthedUser = Depends(get_current_user),
    storage: StorageClient = Depends(get_storage_client),
    repository: Repository = Depends(get_repository),
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
    settings: Settings = Depends(get_settings),
) -> CreateAnalysisResponse:
    # 1. OWNERSHIP, before any Storage call, so a hostile path is never even
    #    dereferenced. A prefix string comparison -- no round trip (Part 2.1).
    expected_prefix = f"{settings.supabase_storage_bucket}/{user.user_id}/"
    if not body.storage_path.startswith(expected_prefix):
        raise ApiError(ErrorCode.STORAGE_PATH_FORBIDDEN)

    # 2. EXISTENCE AND SIZE.
    head = await storage.head_object(body.storage_path)
    # `is not None` and not a truthiness test: a zero-length object is a real
    # measured size, and an absent Content-Length is "unknown", not "zero".
    if head.size_bytes is not None and head.size_bytes > settings.max_upload_bytes:
        raise ApiError(ErrorCode.FILE_TOO_LARGE)

    # 3. CALIBRATION, only when the block is present. Absence is NOT an error
    #    and never blocks anything: it skips Stages 10-11 and the finished
    #    analysis carries ball_speed_mph = null, reason 'not_calibrated'.
    if body.ball_speed_calibration is not None:
        reason = body.ball_speed_calibration.validation_error()
        if reason is not None:
            raise ApiError(ErrorCode.CALIBRATION_INVALID, message=reason)

    # 4. QUEUE DEPTH.
    if not orchestrator.has_capacity():
        raise ApiError(
            ErrorCode.QUEUE_FULL,
            headers={"Retry-After": str(QUEUE_FULL_RETRY_AFTER_S)},
        )

    jobs_ahead = orchestrator.queue_depth
    job_id = uuid.uuid4()
    job = JobRow(
        id=job_id,
        user_id=user.user_id,
        storage_path=body.storage_path,
        status=JobStatus.QUEUED,
        created_at=utcnow(),
    )
    await repository.create_job(job)
    orchestrator.submit(
        job_id=job_id,
        user_id=user.user_id,
        storage_path=body.storage_path,
        request=body,
    )

    response.headers[REQUEST_ID_HEADER] = current_request_id()
    return CreateAnalysisResponse(
        analysis_id=job_id,
        # Always "queued": the row is written before submission and the
        # executor has not necessarily picked it up.
        status=JobStatus.QUEUED,
        poll_url=f"/v1/analyses/{job_id}",
        # Base plus roughly one job's worth per job already ahead. An estimate,
        # labelled as one; on a cold start it WILL read low, which is accepted
        # and documented rather than hidden.
        estimated_seconds=JOB_ESTIMATED_SECONDS_BASE * (jobs_ahead + 1),
        ball_speed_requested=body.ball_speed_calibration is not None,
    )


# ---------------------------------------------------------------------------
# Part 3.4 — GET /v1/analyses (history). Declared BEFORE the {id} route purely
# for readability; the paths are distinct so order does not matter to routing.
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=AnalysisListResponse,
    responses=openapi_errors(
        ErrorCode.INVALID_REQUEST,
        ErrorCode.AUTH_INVALID_TOKEN,
        ErrorCode.INTERNAL_ERROR,
    ),
    summary="List this user's analyses, newest first, keyset-paginated.",
)
async def list_analyses(
    limit: int = Query(default=HISTORY_DEFAULT_LIMIT, ge=1, le=HISTORY_MAX_LIMIT),
    cursor: str | None = Query(default=None),
    user: AuthedUser = Depends(get_current_user),
    repository: Repository = Depends(get_repository),
) -> AnalysisListResponse:
    decoded: tuple[datetime, UUID] | None = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor)
        except ValueError as exc:
            raise ApiError(ErrorCode.INVALID_REQUEST) from exc

    # limit + 1: if the extra row comes back there is another page. The cursor
    # is built from the LAST RETURNED row (the limit-th), not the extra one.
    rows = await repository.list_analyses(user.user_id, limit + 1, decoded)
    has_more = len(rows) > limit
    page = rows[:limit]

    items = [
        AnalysisListItem(
            analysis_id=row.id,
            created_at=row.created_at,
            shot_type=row.shot_type,
            # Never substituted with 0. null means "no score was produced" /
            # "no speed was measured"; 0 would mean "scored zero".
            overall_score=row.overall_score,
            ball_speed_mph=row.ball_speed_mph,
        )
        for row in page
    ]
    next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if (has_more and page) else None
    return AnalysisListResponse(items=items, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Stage 19 — GET /v1/analyses/{analysis_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{analysis_id}",
    # response_model is deliberately None: this route is POLYMORPHIC. Either
    # body is a 200. Both shapes are advertised in `responses` below.
    response_model=None,
    responses={
        200: {
            "description": (
                "Either AnalysisJobStatusResponse (job queued/running/failed) or the full "
                "AnalysisResponse (job finished). The client discriminates on body shape."
            ),
            "model": AnalysisResponse,
        },
        **openapi_errors(
            ErrorCode.AUTH_INVALID_TOKEN,
            ErrorCode.ANALYSIS_NOT_FOUND,
            ErrorCode.INTERNAL_ERROR,
        ),
    },
    summary="Poll one analysis. Body shape depends on job state.",
)
async def get_analysis(
    analysis_id: UUID,
    user: AuthedUser = Depends(get_current_user),
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    # Ownership: a mismatch and a missing id both return 404 analysis_not_found
    # with an identical body. The endpoint does not reveal that an id exists.
    job = await repository.get_job(analysis_id, user.user_id)
    if job is None:
        raise ApiError(ErrorCode.ANALYSIS_NOT_FOUND)

    # --- Body B: finished ------------------------------------------------
    if job.status is JobStatus.SUCCEEDED:
        return await _finished_body(repository, analysis_id, user.user_id)

    # --- Body A: queued / running / failed -------------------------------
    now = utcnow()
    if is_stale(job, now=now, stale_after_s=settings.job_heartbeat_stale_s):
        # Best effort so history and metrics are right; the response must not
        # depend on this write succeeding.
        await mark_worker_lost(repository, job.id)
        return _job_status_body(
            analysis_id,
            JobStatus.FAILED,
            error=AnalysisError(
                code=ErrorCode.WORKER_LOST,
                message=default_message_for(ErrorCode.WORKER_LOST),
                # Honest, not a fabrication: the process that knew the stage is
                # gone, so it is not backfilled with a guess.
                stage=UNKNOWN_STAGE,
                retryable=is_retryable(ErrorCode.WORKER_LOST),
            ),
        )

    if job.status is JobStatus.FAILED:
        code = job.error_code or ErrorCode.INTERNAL_ERROR
        return _job_status_body(
            analysis_id,
            JobStatus.FAILED,
            error=AnalysisError(
                code=code,
                message=job.error_message or default_message_for(code),
                stage=job.error_stage or UNKNOWN_STAGE,
                retryable=is_retryable(code),
            ),
        )

    if job.status is JobStatus.RUNNING:
        elapsed = int((now - job.created_at).total_seconds())
        return _job_status_body(
            analysis_id,
            JobStatus.RUNNING,
            # null, NOT 0: 0 would imply a measured position, and a running job
            # has no position in the queue (Part 3.5).
            queue_position=None,
            estimated_seconds_remaining=max(1, JOB_ESTIMATED_SECONDS_BASE - elapsed),
        )

    # Queued. Position is the count of jobs in ('queued','running') created
    # strictly before it; 1 means "next".
    ahead = await repository.count_active_jobs_before(job.created_at)
    return _job_status_body(
        analysis_id,
        JobStatus.QUEUED,
        queue_position=ahead + 1,
        estimated_seconds_remaining=JOB_ESTIMATED_SECONDS_BASE * (ahead + 1),
    )


def _job_status_body(
    analysis_id: UUID,
    job_status: JobStatus,
    *,
    queue_position: int | None = None,
    estimated_seconds_remaining: int | None = None,
    error: AnalysisError | None = None,
) -> JSONResponse:
    """Body A. Always HTTP 200 -- including for `status: "failed"`.

    The poll succeeded; the job did not. Conflating those two is the most common
    way this contract gets implemented wrong.

    Serialized with `exclude_none=False` so the nulls are present rather than
    stripped, and it carries NO `feedback` and NO `scorecard` key -- not even
    null-valued (REQUIRED INVARIANT, Part 4.4b).
    """
    body = AnalysisJobStatusResponse(
        analysis_id=analysis_id,
        status=job_status,
        queue_position=queue_position,
        estimated_seconds_remaining=estimated_seconds_remaining,
        error=error,
    )
    return _json(body.model_dump(mode="json", exclude_none=False))


async def _finished_body(
    repository: Repository, analysis_id: UUID, user_id: UUID
) -> JSONResponse:
    """Body B: the stored `analyses.payload`, returned verbatim.

    REQUIRED INVARIANT (Part 4.4a): this must NEVER emit `status: "succeeded"`.
    `succeeded` is the DATABASE value; the wire value on a finished job is the
    full AnalysisResponse with a status of `complete` or `partial`. The client's
    `JobStatus.fromJson` silently defaults an unknown status to `queued`, so
    emitting `succeeded` here produces an infinite spinner on a job that
    finished successfully -- close to the hardest possible bug to diagnose from
    a user report. A payload that violates it is refused as `internal_error`
    rather than repaired, because repairing it would mean inventing a status.
    """
    payload = await repository.get_analysis_payload(analysis_id, user_id)
    if payload is None:
        # The job says succeeded but no analyses row exists. Part 3.5 calls this
        # out as the crash-between-writes case; it is a server fault, not a 404.
        logger.error("job %s is succeeded but has no analyses row", analysis_id)
        raise ApiError(ErrorCode.INTERNAL_ERROR)

    payload_status = payload.get("status")
    if payload_status not in {s.value for s in AnalysisStatus}:
        logger.error(
            "stored payload for %s has status %r, which is not an AnalysisStatus",
            analysis_id,
            payload_status,
        )
        raise ApiError(ErrorCode.INTERNAL_ERROR)

    return _json(payload)
