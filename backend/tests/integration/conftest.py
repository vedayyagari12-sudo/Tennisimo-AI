"""Offline harness for the HTTP layer.

No network, no real video, no MediaPipe, no Gemini. Every external boundary is a
fake injected through ``app.dependency_overrides``:

* ``get_token_verifier``  -> ``FakeTokenVerifier``   (no JWKS fetch)
* ``get_storage_client``  -> ``FakeStorageClient``   (no Supabase Storage)
* ``get_repository``      -> ``FakeRepository``      (no PostgREST)
* ``get_orchestrator``    -> ``FakeOrchestrator``    (no executor thread)
* ``get_auth_admin_client`` -> ``FakeAuthAdminClient`` (no GoTrue Admin API)

Only the verifier's *construction* is real at startup; ``PyJWKClient`` performs
no I/O until a key is requested, and the fake means it never is.

Every credential-shaped value in this file is obviously fake and points at the
reserved ``.invalid`` TLD, which cannot resolve.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from app.api.auth import AuthedUser, get_current_user, get_token_verifier
from app.api.errors import ApiError
from app.config import get_settings
from app.main import create_app
from app.models.enums import ErrorCode, JobStatus
from app.models.requests import CreateAnalysisRequest
from app.services.auth_admin import get_auth_admin_client
from app.services.orchestrator import get_orchestrator
from app.services.repository import (
    AnalysisListRow,
    AnalysisRow,
    JobRow,
    get_repository,
)
from app.services.storage import ObjectHead, SignedUpload, get_storage_client

# Obviously-fake environment. Nothing here is a credential.
FAKE_ENV: dict[str, str] = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}

VALID_TOKEN = "fake.valid.token"
OTHER_TOKEN = "fake.other-user.token"
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def auth_headers(token: str = VALID_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def storage_path_for(user_id: UUID = USER_ID, name: str = "clip.mp4") -> str:
    return f"swing-videos/{user_id}/{name}"


class FakeTokenVerifier:
    """Maps a known fake token to a user; anything else is a 401."""

    def __init__(self) -> None:
        self.tokens: dict[str, AuthedUser] = {
            VALID_TOKEN: AuthedUser(user_id=USER_ID, email="a@example.invalid", raw_claims={}),
            OTHER_TOKEN: AuthedUser(user_id=OTHER_USER_ID, email=None, raw_claims={}),
        }

    def verify(self, token: str) -> AuthedUser:
        user = self.tokens.get(token)
        if user is None:
            raise ApiError(ErrorCode.AUTH_INVALID_TOKEN)
        return user


class FakeStorageClient:
    """In-memory Storage. ``head_error`` lets a test force a Storage failure."""

    def __init__(self) -> None:
        self.head_result = ObjectHead(size_bytes=1024, content_type="video/mp4")
        self.head_error: Exception | None = None
        self.signed_error: Exception | None = None
        self.signed_calls: list[str] = []
        self.head_calls: list[str] = []
        # Account deletion: full "<bucket>/<uid>/<name>" keys held in Storage.
        self.objects: set[str] = set()
        self.delete_folder_calls: list[str] = []
        self.delete_folder_error: Exception | None = None
        self.events: list[str] = []

    async def create_signed_upload_url(self, storage_path: str) -> SignedUpload:
        self.signed_calls.append(storage_path)
        if self.signed_error is not None:
            raise self.signed_error
        return SignedUpload(
            upload_url=f"https://fake-project.supabase.invalid/storage/v1/object/upload/sign/{storage_path}",
            upload_token="fake-upload-token",
            expires_at=datetime.now(UTC) + timedelta(seconds=7200),
        )

    async def head_object(self, storage_path: str) -> ObjectHead:
        self.head_calls.append(storage_path)
        if self.head_error is not None:
            raise self.head_error
        return self.head_result

    async def download_to_path(self, storage_path: str, destination: Path) -> int:
        destination.write_bytes(b"")
        return 0

    async def delete_folder(self, folder_path: str) -> int:
        self.events.append("storage")
        self.delete_folder_calls.append(folder_path)
        if self.delete_folder_error is not None:
            raise self.delete_folder_error
        doomed = {key for key in self.objects if key.startswith(f"{folder_path}/")}
        self.objects -= doomed
        return len(doomed)


class FakeRepository:
    """In-memory stand-in for the Part 1 schema."""

    def __init__(self) -> None:
        self.jobs: dict[UUID, JobRow] = {}
        self.payloads: dict[UUID, dict[str, Any]] = {}
        self.analyses: list[tuple[UUID, AnalysisListRow]] = []  # (user_id, row)
        self.failures: list[tuple[UUID, ErrorCode, str, str]] = []
        self.list_error: Exception | None = None
        self.delete_rows_calls: list[UUID] = []
        self.events: list[str] = []

    # -- account deletion ------------------------------------------------
    async def delete_user_rows(self, user_id: UUID) -> None:
        self.events.append("rows")
        self.delete_rows_calls.append(user_id)
        doomed = {row.id for owner, row in self.analyses if owner == user_id}
        self.analyses = [(owner, row) for owner, row in self.analyses if owner != user_id]
        for analysis_id in doomed:
            self.payloads.pop(analysis_id, None)
        self.jobs = {jid: job for jid, job in self.jobs.items() if job.user_id != user_id}

    # -- jobs ------------------------------------------------------------
    async def create_job(self, job: JobRow) -> None:
        self.jobs[job.id] = job

    async def get_job(self, job_id: UUID, user_id: UUID) -> JobRow | None:
        job = self.jobs.get(job_id)
        if job is None or job.user_id != user_id:
            return None
        return job

    async def count_active_jobs(self) -> int:
        return sum(
            1 for j in self.jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        )

    async def count_active_jobs_before(self, created_at: datetime) -> int:
        return sum(
            1
            for j in self.jobs.values()
            if j.status in (JobStatus.QUEUED, JobStatus.RUNNING) and j.created_at < created_at
        )

    async def mark_running(self, job_id: UUID, at: datetime) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = job.model_copy(
            update={"status": JobStatus.RUNNING, "heartbeat_at": at}
        )

    async def heartbeat(self, job_id: UUID, at: datetime) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = job.model_copy(update={"heartbeat_at": at})

    async def mark_failed(
        self, job_id: UUID, error_code: ErrorCode, message: str, stage: str
    ) -> None:
        self.failures.append((job_id, error_code, message, stage))
        job = self.jobs.get(job_id)
        if job is not None:
            self.jobs[job_id] = job.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "error_code": error_code,
                    "error_message": message,
                    "error_stage": stage,
                }
            )

    async def persist_success(self, analysis: AnalysisRow) -> None:
        self.payloads[analysis.id] = analysis.payload
        job = self.jobs.get(analysis.id)
        if job is not None:
            self.jobs[analysis.id] = job.model_copy(update={"status": JobStatus.SUCCEEDED})

    # -- analyses --------------------------------------------------------
    async def get_analysis_payload(
        self, analysis_id: UUID, user_id: UUID
    ) -> dict[str, Any] | None:
        for owner, row in self.analyses:
            if row.id == analysis_id and owner == user_id:
                return self.payloads.get(analysis_id)
        return None

    async def list_analyses(
        self, user_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None
    ) -> list[AnalysisListRow]:
        if self.list_error is not None:
            raise self.list_error
        rows = [row for owner, row in self.analyses if owner == user_id]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if cursor is not None:
            rows = [r for r in rows if (r.created_at, r.id) < cursor]
        return rows[:limit]

    # -- test helpers ----------------------------------------------------
    def seed_job(self, **overrides: Any) -> JobRow:
        job = JobRow(
            id=overrides.pop("id", uuid4()),
            user_id=overrides.pop("user_id", USER_ID),
            storage_path=overrides.pop("storage_path", storage_path_for()),
            status=overrides.pop("status", JobStatus.QUEUED),
            created_at=overrides.pop("created_at", datetime.now(UTC)),
            **overrides,
        )
        self.jobs[job.id] = job
        return job

    def seed_analysis(
        self,
        *,
        analysis_id: UUID,
        created_at: datetime,
        user_id: UUID = USER_ID,
        payload: dict[str, Any] | None = None,
        shot_type: str = "forehand_topspin",
        overall_score: float | None = 74.5,
        ball_speed_mph: int | None = 68,
        pipeline_version: str = "v3",
    ) -> AnalysisListRow:
        row = AnalysisListRow(
            id=analysis_id,
            created_at=created_at,
            shot_type=shot_type,
            overall_score=overall_score,
            ball_speed_mph=ball_speed_mph,
            pipeline_version=pipeline_version,
        )
        self.analyses.append((user_id, row))
        if payload is not None:
            self.payloads[analysis_id] = payload
        return row


class FakeAuthAdminClient:
    """In-memory Auth users. Deleting an absent user is a no-op, like GoTrue's 404."""

    def __init__(self) -> None:
        self.users: set[UUID] = {USER_ID, OTHER_USER_ID}
        self.delete_calls: list[UUID] = []
        self.delete_error: Exception | None = None
        self.events: list[str] = []

    async def delete_user(self, user_id: UUID) -> None:
        self.events.append("auth")
        self.delete_calls.append(user_id)
        if self.delete_error is not None:
            raise self.delete_error
        self.users.discard(user_id)


class FakeOrchestrator:
    """No executor, no thread. Records submissions; depth is settable."""

    def __init__(self, max_depth: int = 4) -> None:
        self.depth = 0
        self.max_depth = max_depth
        self.submitted: list[dict[str, Any]] = []

    @property
    def queue_depth(self) -> int:
        return self.depth

    def has_capacity(self) -> bool:
        return self.depth < self.max_depth

    def submit(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
        storage_path: str,
        request: CreateAnalysisRequest,
    ) -> None:
        self.submitted.append(
            {
                "job_id": job_id,
                "user_id": user_id,
                "storage_path": storage_path,
                "request": request,
            }
        )
        self.depth += 1


@pytest.fixture()
def fake_storage() -> FakeStorageClient:
    return FakeStorageClient()


@pytest.fixture()
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture()
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture()
def fake_auth_admin() -> FakeAuthAdminClient:
    return FakeAuthAdminClient()


@pytest.fixture()
def client(
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: FakeStorageClient,
    fake_repository: FakeRepository,
    fake_orchestrator: FakeOrchestrator,
    fake_auth_admin: FakeAuthAdminClient,
):
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    app = create_app()
    verifier = FakeTokenVerifier()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_storage_client] = lambda: fake_storage
    app.dependency_overrides[get_repository] = lambda: fake_repository
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    app.dependency_overrides[get_auth_admin_client] = lambda: fake_auth_admin

    # raise_server_exceptions=False so the registered Exception handler runs and
    # its 500 envelope can be asserted, instead of the exception being re-raised
    # into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    get_settings.cache_clear()


__all__ = [
    "FAKE_ENV",
    "OTHER_TOKEN",
    "OTHER_USER_ID",
    "USER_ID",
    "VALID_TOKEN",
    "FakeAuthAdminClient",
    "FakeOrchestrator",
    "FakeRepository",
    "FakeStorageClient",
    "FakeTokenVerifier",
    "auth_headers",
    "get_current_user",
    "storage_path_for",
]
