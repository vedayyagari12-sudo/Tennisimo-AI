"""Read/write layer over the DATABASE_SETUP.md Part 1 schema, via PostgREST.

httpx, not `supabase-py` -- see the dependency decision in `storage.py`.

The backend holds the **service-role key**, which BYPASSES RLS (Part 2.1). RLS
is therefore NOT what isolates users on this path: every read here filters on
`user_id` explicitly, and a row belonging to another user is reported to the
route as absent so it can answer `404 analysis_not_found` without confirming
the id exists.

Two things in here are pure and unit-tested on their own: `encode_cursor` /
`decode_cursor` (the keyset cursor codec, Part 3.4).
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import UTC, datetime
from typing import Any, Final, Protocol
from uuid import UUID

import httpx
from fastapi import Request
from pydantic import BaseModel, ConfigDict

from app.api.errors import ApiError
from app.config import Settings
from app.models.enums import (
    AnalysisStatus,
    BallSpeedUnavailableReason,
    ErrorCode,
    JobStatus,
    ShotType,
)

logger = logging.getLogger("tennisform.repository")

CURSOR_SEPARATOR: Final[str] = "|"
ACTIVE_JOB_STATUSES: Final[tuple[JobStatus, ...]] = (JobStatus.QUEUED, JobStatus.RUNNING)


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------


class JobRow(BaseModel):
    """One `analysis_jobs` row."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    storage_path: str
    status: JobStatus
    error_code: ErrorCode | None = None
    error_message: str | None = None
    error_stage: str | None = None
    heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class AnalysisRow(BaseModel):
    """One `analyses` row, as written by Stage 18.

    `ball_speed_mph` / `ball_speed_unavailable_reason` carry the same pairing
    invariant as `BallSpeedResult` (`responses.py`) -- exactly one is non-null
    -- and the live `analyses_ball_speed_reason_pairing_chk` enforces it in the
    database. No validator is repeated here: Stage 18 builds both fields from a
    single already-validated `BallSpeedResult`, whose own `model_validator`
    guarantees the pairing before this row can be constructed.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    storage_path: str
    status: AnalysisStatus
    shot_type: ShotType
    overall_score: float | None = None
    ball_speed_mph: int | None = None
    ball_speed_unavailable_reason: BallSpeedUnavailableReason | None = None
    pipeline_version: str
    rubric_version: str
    payload: dict[str, Any]
    created_at: datetime | None = None


class AnalysisListRow(BaseModel):
    """The FLAT six-value projection `GET /v1/analyses` serves (Part 3.4/4.5).

    Read entirely from indexed columns; `payload` is never touched.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    created_at: datetime
    shot_type: ShotType
    overall_score: float | None = None
    ball_speed_mph: int | None = None
    # `analyses.pipeline_version` is `text NOT NULL` (Part 1), so every row has one.
    pipeline_version: str


# ---------------------------------------------------------------------------
# Cursor codec (PURE)
# ---------------------------------------------------------------------------


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    """``base64url("<created_at>|<id>")``, unpadded (Part 3.4).

    PURE: no I/O. The cursor is opaque to the client, unsigned, and is NOT a
    security boundary -- `user_id = jwt.sub` is, and it is applied regardless of
    cursor contents. A forged cursor can only page through the forger's own rows.
    """
    raw = f"{created_at.isoformat()}{CURSOR_SEPARATOR}{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of `encode_cursor`. Raises ``ValueError`` on anything undecodable."""
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("cursor is not valid base64url") from exc
    created_raw, separator, id_raw = raw.partition(CURSOR_SEPARATOR)
    if not separator:
        raise ValueError("cursor is missing its separator")
    created_at = datetime.fromisoformat(created_raw)  # ValueError propagates
    return created_at, UUID(id_raw)  # ValueError propagates


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class Repository(Protocol):
    """Everything the API layer and the job runner need from the database."""

    async def create_job(self, job: JobRow) -> None: ...

    async def get_job(self, job_id: UUID, user_id: UUID) -> JobRow | None: ...

    async def count_active_jobs(self) -> int: ...

    async def count_active_jobs_before(self, created_at: datetime) -> int: ...

    async def mark_running(self, job_id: UUID, at: datetime) -> None: ...

    async def heartbeat(self, job_id: UUID, at: datetime) -> None: ...

    async def mark_failed(
        self, job_id: UUID, error_code: ErrorCode, message: str, stage: str
    ) -> None: ...

    async def persist_success(self, analysis: AnalysisRow) -> None: ...

    async def get_analysis_payload(
        self, analysis_id: UUID, user_id: UUID
    ) -> dict[str, Any] | None: ...

    async def list_analyses(
        self, user_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
    ) -> list[AnalysisListRow]: ...


# ---------------------------------------------------------------------------
# PostgREST implementation
# ---------------------------------------------------------------------------


class SupabaseRepository:
    """Concrete `Repository` over PostgREST."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._client = client
        self._base = f"{settings.supabase_url}/rest/v1"
        key = settings.supabase_service_role_key.get_secret_value()
        self._headers: dict[str, str] = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = await self._client.request(
                method, f"{self._base}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("postgrest %s %s failed: %s", method, path, type(exc).__name__)
            raise ApiError(ErrorCode.INTERNAL_ERROR) from exc
        if response.status_code >= 400:
            logger.warning("postgrest %s %s returned %s", method, path, response.status_code)
            raise ApiError(ErrorCode.INTERNAL_ERROR)
        return response

    async def create_job(self, job: JobRow) -> None:
        await self._request(
            "POST",
            "/analysis_jobs",
            json=job.model_dump(mode="json", exclude_none=True),
            headers={"Prefer": "return=minimal"},
        )

    async def get_job(self, job_id: UUID, user_id: UUID) -> JobRow | None:
        response = await self._request(
            "GET",
            f"/analysis_jobs?id=eq.{job_id}&user_id=eq.{user_id}&select=*&limit=1",
        )
        rows = response.json()
        return JobRow.model_validate(rows[0]) if rows else None

    async def count_active_jobs(self) -> int:
        statuses = ",".join(f'"{s.value}"' for s in ACTIVE_JOB_STATUSES)
        response = await self._request(
            "GET",
            f"/analysis_jobs?status=in.({statuses})&select=id",
            headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
        )
        return _content_range_total(response)

    async def count_active_jobs_before(self, created_at: datetime) -> int:
        statuses = ",".join(f'"{s.value}"' for s in ACTIVE_JOB_STATUSES)
        response = await self._request(
            "GET",
            f"/analysis_jobs?status=in.({statuses})"
            f"&created_at=lt.{created_at.isoformat()}&select=id",
            headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
        )
        return _content_range_total(response)

    async def mark_running(self, job_id: UUID, at: datetime) -> None:
        await self._request(
            "PATCH",
            f"/analysis_jobs?id=eq.{job_id}&status=eq.{JobStatus.QUEUED.value}",
            json={"status": JobStatus.RUNNING.value, "heartbeat_at": at.isoformat()},
            headers={"Prefer": "return=minimal"},
        )

    async def heartbeat(self, job_id: UUID, at: datetime) -> None:
        await self._request(
            "PATCH",
            f"/analysis_jobs?id=eq.{job_id}&status=eq.{JobStatus.RUNNING.value}",
            json={"heartbeat_at": at.isoformat()},
            headers={"Prefer": "return=minimal"},
        )

    async def mark_failed(
        self, job_id: UUID, error_code: ErrorCode, message: str, stage: str
    ) -> None:
        await self._request(
            "PATCH",
            f"/analysis_jobs?id=eq.{job_id}",
            json={
                "status": JobStatus.FAILED.value,
                "error_code": error_code.value,
                "error_message": message,
                "error_stage": stage,
            },
            headers={"Prefer": "return=minimal"},
        )

    async def persist_success(self, analysis: AnalysisRow) -> None:
        """Stage 18.

        KNOWN GAP, stated rather than hidden: Part 3.5 requires the `analyses`
        INSERT and the `analysis_jobs` UPDATE to be ONE transaction. PostgREST
        exposes no multi-statement transaction, so this is two requests. The
        order is chosen so the failure mode is the benign one -- the analysis
        row exists but the job still reads `running` and eventually goes stale
        (`worker_lost`, retryable) -- rather than the malignant one, a job
        reporting `succeeded` with no row to return, which would 500 the poll
        endpoint forever. Closing this needs a Postgres function called over
        `/rpc/`, which belongs with the DDL and is not created here.
        """
        await self._request(
            "POST",
            "/analyses",
            json=analysis.model_dump(mode="json", exclude_none=True),
            headers={"Prefer": "return=minimal"},
        )
        await self._request(
            "PATCH",
            f"/analysis_jobs?id=eq.{analysis.id}",
            json={"status": JobStatus.SUCCEEDED.value},
            headers={"Prefer": "return=minimal"},
        )

    async def get_analysis_payload(
        self, analysis_id: UUID, user_id: UUID
    ) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            f"/analyses?id=eq.{analysis_id}&user_id=eq.{user_id}&select=payload&limit=1",
        )
        rows = response.json()
        if not rows:
            return None
        payload = rows[0].get("payload")
        return payload if isinstance(payload, dict) else None

    async def list_analyses(
        self, user_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
    ) -> list[AnalysisListRow]:
        """Keyset page, newest first. Fetches `limit` rows; the caller asks for
        `limit + 1` to detect a next page.

        DEVIATION FROM Part 1.1, recorded: the spec's SQL uses the row-wise
        comparison `(created_at, id) < (ts, cid)` and explicitly says not to
        rewrite it as the OR form, because the planner handles the row form
        better. PostgREST has **no syntax for a row-wise comparison**, so the OR
        form is the only thing expressible over this transport. The result set
        is identical; only the plan may differ. If the `EXPLAIN` check in
        Part 1.1 shows a Sort node under load, the fix is a Postgres function
        called over `/rpc/` that runs the row-comparison form verbatim.
        """
        query = (
            f"/analyses?user_id=eq.{user_id}"
            "&select=id,created_at,shot_type,overall_score,ball_speed_mph,pipeline_version"
            "&order=created_at.desc,id.desc"
            f"&limit={limit}"
        )
        if cursor is not None:
            created_at, row_id = cursor
            stamp = created_at.isoformat()
            query += f"&or=(created_at.lt.{stamp},and(created_at.eq.{stamp},id.lt.{row_id}))"
        response = await self._request("GET", query)
        return [AnalysisListRow.model_validate(row) for row in response.json()]


def _content_range_total(response: httpx.Response) -> int:
    """Parse PostgREST's ``Content-Range: 0-0/12`` exact count."""
    header = response.headers.get("content-range", "")
    _, _, total = header.partition("/")
    try:
        return int(total)
    except ValueError:
        logger.warning("postgrest returned an unparseable Content-Range: %r", header)
        raise ApiError(ErrorCode.INTERNAL_ERROR) from None


def utcnow() -> datetime:
    """Single source of 'now' for the job state machine, so tests can patch one thing."""
    return datetime.now(UTC)


def get_repository(request: Request) -> Repository:
    """Dependency returning the startup-built repository from ``app.state``."""
    repository: Repository | None = getattr(request.app.state, "repository", None)
    if repository is None:  # pragma: no cover - misconfigured app
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    return repository
