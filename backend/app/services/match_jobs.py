"""Match mode jobs (2026-11-09). IMPURE glue around the pure court engine.

``POST /v1/matches`` writes a ``queued`` record and hands the job to a
one-worker executor of its own, so a long match never holds up a swing
analysis (which has its own one-worker executor). The worker downloads the
video, runs ``court_offline.analyze_court_video``, writes the finished record,
and deletes the video (SESSION_MODE.md Decision 3) whether it succeeded or
not.

Records are JSON objects in the user's storage folder
(``{bucket}/{user_id}/matches/{match_id}.json``; ``app/models/match.py``).
The worker rewrites its record with progress at most every
``PROGRESS_WRITE_S``; a ``queued``/``running`` record this process is not
working on and that has not moved for ``STALE_AFTER_S`` belonged to a worker
that is gone (deploy, restart) and reads as failed ``worker_lost``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol
from uuid import UUID

from fastapi import Request

from app.api.errors import ApiError, default_message_for
from app.config import Settings
from app.models.court import CourtAnalysisResponse, DepthZone, LineVerdict
from app.models.enums import ErrorCode
from app.models.match import (
    CalibrationSource,
    CreateMatchRequest,
    MatchRecord,
    MatchStats,
    MatchSummary,
    SessionKind,
)
from app.models.responses import CoachingFeedback
from app.services.storage import ObjectHead

logger = logging.getLogger("tennisform.matches")

#: Queued plus running matches this process accepts before ``queue_full``.
MAX_QUEUED_MATCHES: Final[int] = 3
PROGRESS_WRITE_S: Final[float] = 10.0
STALE_AFTER_S: Final[float] = 180.0
#: The list shows at most this many matches, newest first.
MAX_LISTED: Final[int] = 50
MATCH_FOLDER: Final[str] = "matches"
#: Longest a worker waits on one storage call made through the app loop.
AWAIT_TIMEOUT_S: Final[float] = 120.0
JSON_CONTENT_TYPE: Final[str] = "application/json"
_VIDEO_NAME: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F-]{36}\.(mp4|mov|webm)$")


class MatchStore(Protocol):
    async def put_bytes(self, storage_path: str, body: bytes, content_type: str) -> None: ...

    async def get_bytes(self, storage_path: str) -> bytes | None: ...

    async def list_names(self, folder_path: str) -> list[str]: ...

    async def delete_object(self, storage_path: str) -> None: ...

    async def head_object(self, storage_path: str) -> ObjectHead: ...

    async def download_to_path(self, storage_path: str, destination: Path) -> int: ...


#: (result, where the court came from, seconds analysed, court picture JPEG)
AnalysisOutput = tuple[CourtAnalysisResponse, CalibrationSource | None, float | None, bytes | None]
Analyzer = Callable[[Path, CreateMatchRequest, Callable[[float], None]], AnalysisOutput]
#: Computed numbers in, coaching text out. Must not raise.
Coach = Callable[[CourtAnalysisResponse], CoachingFeedback]

#: A rally video is short, so the summary needs far fewer landings than the
#: 30 a whole session does before the model is asked to write about them.
RALLY_MIN_LANDINGS_FOR_GEMINI: Final[int] = 6


# --------------------------------------------------------------- pure parts


def utcnow() -> datetime:
    return datetime.now(UTC)


def match_folder(bucket: str, user_id: UUID) -> str:
    return f"{bucket}/{user_id}/{MATCH_FOLDER}"


def record_path(bucket: str, user_id: UUID, match_id: UUID) -> str:
    return f"{match_folder(bucket, user_id)}/{match_id}.json"


def picture_path(bucket: str, user_id: UUID, match_id: UUID) -> str:
    return f"{match_folder(bucket, user_id)}/{match_id}.jpg"


def owns_video(storage_path: str, bucket: str, user_id: UUID) -> bool:
    """A match video is one the upload ticket issued to this user:
    ``{bucket}/{user_id}/{uuid}.{ext}``, nothing else."""
    prefix = f"{bucket}/{user_id}/"
    return storage_path.startswith(prefix) and bool(_VIDEO_NAME.match(storage_path[len(prefix):]))


def video_objects(request: CreateMatchRequest) -> list[str]:
    """The stored objects that make up the video, in byte order."""
    if request.part_count == 0:
        return [request.storage_path]
    return [f"{request.storage_path}.part{index:03d}" for index in range(request.part_count)]


def match_stats(result: CourtAnalysisResponse) -> MatchStats:
    """Headline numbers from one result. Rally lengths use each rally's best
    estimate (``length_hits``); IN% counts only landings that were called."""
    landings = result.landings
    calls = [l.line_call.verdict for l in landings if l.line_call is not None]
    in_count = sum(1 for v in calls if v is LineVerdict.IN)
    out_count = sum(1 for v in calls if v is LineVerdict.OUT)
    lengths = [r.length_hits for r in result.rallies]
    deep = sum(1 for l in landings if l.depth_zone is DepthZone.DEEP)
    return MatchStats(
        shots_landed=len(landings),
        rallies=len(result.rallies),
        longest_rally_hits=max(lengths) if lengths else None,
        average_rally_hits=round(sum(lengths) / len(lengths), 1) if lengths else None,
        in_count=in_count,
        out_count=out_count,
        in_percent=round(100.0 * in_count / (in_count + out_count), 1) if in_count + out_count else None,
        deep_percent=round(100.0 * deep / len(landings), 1) if landings else None,
    )


def is_stale(record: MatchRecord, *, now: datetime, active: bool) -> bool:
    return (
        record.status in ("queued", "running")
        and not active
        and record.updated_at < now - timedelta(seconds=STALE_AFTER_S)
    )


def run_match_analysis(
    video: Path, request: CreateMatchRequest, progress: Callable[[float], None]
) -> AnalysisOutput:
    """Decode and analyse one match video. Imported lazily: OpenCV and the
    court engine are not needed to serve a route."""
    from app.services.court_offline import (
        analyze_court_video,
        court_picture_jpeg,
        project_court_result,
    )

    inputs, result = analyze_court_video(
        video, request.calibration, court_format=request.court_format, progress=progress,
        track_players=True,
    )
    response = project_court_result(result, frame_size=inputs.track_size or (1, 1))
    times = inputs.frame_times_s
    duration = result.analysed_until_s
    if duration is None and times.size:
        duration = float(times[-1] - times[0])
    picture: bytes | None = None
    verdict = inputs.calibration_verdict
    if inputs.calibration_frame is not None and verdict is not None and verdict.calibration is not None:
        picture = court_picture_jpeg(inputs.calibration_frame, verdict.calibration)
    return response, inputs.calibration_source, duration, picture


def gemini_rally_coach(settings: Settings) -> Coach:
    """The production coach: the court summary (``feedback/court_summary.py``)
    with Gemini as the writer. Gemini only words the computed numbers; its
    text passes the court numeric guard or the template is used instead."""

    def coach(result: CourtAnalysisResponse) -> CoachingFeedback:
        from app.feedback.court_summary import generate_court_summary
        from app.feedback.gemini import GoogleGenAIClient
        from app.services.pipeline import to_wire_feedback

        client = GoogleGenAIClient(
            api_key=settings.gemini_api_key.get_secret_value(), model=settings.gemini_model
        )
        return to_wire_feedback(
            generate_court_summary(result, client, min_landings=RALLY_MIN_LANDINGS_FOR_GEMINI)
        )

    return coach


# --------------------------------------------------------------- the jobs


class MatchJobs:
    def __init__(
        self,
        *,
        store: MatchStore,
        settings: Settings,
        loop: asyncio.AbstractEventLoop,
        analyze: Analyzer = run_match_analysis,
        coach: Coach | None = None,
    ) -> None:
        self._store = store
        self._bucket = settings.supabase_storage_bucket
        self._loop = loop
        self._analyze = analyze
        self._coach = coach if coach is not None else gemini_rally_coach(settings)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="match")
        self._lock = threading.Lock()
        self._active: set[UUID] = set()

    # -- routes

    async def create(self, user_id: UUID, request: CreateMatchRequest) -> tuple[MatchRecord, Future[None]]:
        if not owns_video(request.storage_path, self._bucket, user_id):
            raise ApiError(ErrorCode.STORAGE_PATH_FORBIDDEN)
        with self._lock:
            if len(self._active) >= MAX_QUEUED_MATCHES:
                raise ApiError(ErrorCode.QUEUE_FULL)
        for path in video_objects(request):
            await self._store.head_object(path)  # 404 object_not_found
        now = utcnow()
        record = MatchRecord(
            match_id=uuid.uuid4(),
            status="queued",
            created_at=now,
            updated_at=now,
            title=request.title,
            kind=request.kind,
            court_format=request.court_format,
            progress=0.0,
        )
        await self._write(user_id, record)
        with self._lock:
            self._active.add(record.match_id)
        try:
            future = self._executor.submit(self._run, user_id, record, request)
        except Exception:  # pragma: no cover - executor shut down
            with self._lock:
                self._active.discard(record.match_id)
            raise
        return record, future

    async def get(self, user_id: UUID, match_id: UUID) -> MatchRecord | None:
        raw = await self._store.get_bytes(record_path(self._bucket, user_id, match_id))
        if raw is None:
            return None
        record = MatchRecord.model_validate_json(raw)
        with self._lock:
            active = record.match_id in self._active
        if is_stale(record, now=utcnow(), active=active):
            record = record.model_copy(
                update={
                    "status": "failed",
                    "error_code": ErrorCode.WORKER_LOST,
                    "error_message": default_message_for(ErrorCode.WORKER_LOST),
                    "progress": None,
                    "updated_at": utcnow(),
                }
            )
            try:
                await self._write(user_id, record)
            except ApiError:
                logger.warning("best-effort worker_lost write-back failed for match %s", match_id)
        return record

    async def list(self, user_id: UUID, kind: SessionKind | None = None) -> list[MatchSummary]:
        names = await self._store.list_names(match_folder(self._bucket, user_id))
        summaries: list[MatchSummary] = []
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                match_id = UUID(name[: -len(".json")])
            except ValueError:
                continue
            record = await self.get(user_id, match_id)
            if record is not None and (kind is None or record.kind == kind):
                summaries.append(record.summary())
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries[:MAX_LISTED]

    async def delete(self, user_id: UUID, match_id: UUID) -> bool:
        path = record_path(self._bucket, user_id, match_id)
        if await self._store.get_bytes(path) is None:
            return False
        await self._store.delete_object(picture_path(self._bucket, user_id, match_id))
        await self._store.delete_object(path)
        return True

    async def court_picture(self, user_id: UUID, match_id: UUID) -> bytes | None:
        return await self._store.get_bytes(picture_path(self._bucket, user_id, match_id))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- the worker thread

    def _run(self, user_id: UUID, record: MatchRecord, request: CreateMatchRequest) -> None:
        try:
            record = record.model_copy(update={"status": "running", "updated_at": utcnow()})
            self._await(self._write(user_id, record))
            last_write = time.monotonic()

            def progress(fraction: float) -> None:
                nonlocal record, last_write
                if time.monotonic() - last_write < PROGRESS_WRITE_S:
                    return
                last_write = time.monotonic()
                record = record.model_copy(
                    update={"progress": round(0.05 + 0.9 * fraction, 3), "updated_at": utcnow()}
                )
                try:
                    self._await(self._write(user_id, record))
                except ApiError:
                    logger.warning("progress write failed for match %s", record.match_id)

            with tempfile.TemporaryDirectory(prefix="tennisform-match-") as workdir:
                video = Path(workdir) / "match.mp4"
                piece = Path(workdir) / "piece"
                with video.open("wb") as joined:
                    for path in video_objects(request):
                        # One piece on disk at a time, appended in byte order:
                        # the joined file is byte-for-byte the original.
                        self._await(self._store.download_to_path(path, piece))
                        with piece.open("rb") as part:
                            shutil.copyfileobj(part, joined)
                        piece.unlink()
                result, source, duration, picture = self._analyze(video, request, progress)
            feedback = self._feedback(record, result)
            if picture is not None:
                self._await(
                    self._store.put_bytes(
                        picture_path(self._bucket, user_id, record.match_id), picture, "image/jpeg"
                    )
                )
            record = record.model_copy(
                update={
                    "status": "complete",
                    "progress": 1.0,
                    "updated_at": utcnow(),
                    "court_status": result.status.value,
                    "calibration_source": source,
                    "has_court_picture": picture is not None,
                    "duration_s": duration,
                    "result": result,
                    "stats": match_stats(result),
                    "feedback": feedback,
                }
            )
            self._await(self._write(user_id, record))
        except Exception as exc:  # noqa: BLE001 - a dead job must still leave a record
            code = exc.error_code if isinstance(exc, ApiError) else _decode_error_code(exc)
            if code is ErrorCode.INTERNAL_ERROR:
                logger.exception("match %s crashed", record.match_id)
            failed = record.model_copy(
                update={
                    "status": "failed",
                    "progress": None,
                    "updated_at": utcnow(),
                    "error_code": code,
                    "error_message": default_message_for(code),
                }
            )
            try:
                self._await(self._write(user_id, failed))
            except Exception:  # noqa: BLE001 - the staleness rule is the net
                logger.exception("could not mark match %s failed", record.match_id)
        finally:
            with self._lock:
                self._active.discard(record.match_id)
            for path in video_objects(request):
                try:
                    self._await(self._store.delete_object(path))
                except Exception:  # noqa: BLE001 - best effort; the record is what matters
                    logger.warning("could not delete a video piece of match %s", record.match_id)

    def _feedback(self, record: MatchRecord, result: CourtAnalysisResponse) -> CoachingFeedback | None:
        """Rallies get an overall summary. A coaching failure never fails the
        rally: the numbers are still worth showing without the words."""
        if record.kind != "rally":
            return None
        try:
            return self._coach(result)
        except Exception:  # noqa: BLE001
            logger.exception("rally %s coaching failed", record.match_id)
            return None

    async def _write(self, user_id: UUID, record: MatchRecord) -> None:
        await self._store.put_bytes(
            record_path(self._bucket, user_id, record.match_id),
            record.model_dump_json().encode("utf-8"),
            JSON_CONTENT_TYPE,
        )

    def _await(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run a storage call on the app loop from the worker thread. Bounded:
        if the loop has gone away (shutdown), the worker gives up instead of
        waiting forever and holding the process open."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=AWAIT_TIMEOUT_S)

    def idle(self) -> bool:
        with self._lock:
            return not self._active


def _decode_error_code(exc: Exception) -> ErrorCode:
    code = getattr(exc, "error_code", None)
    return code if isinstance(code, ErrorCode) else ErrorCode.INTERNAL_ERROR


def get_match_jobs(request: Request) -> MatchJobs:
    jobs: MatchJobs | None = getattr(request.app.state, "match_jobs", None)
    if jobs is None:  # pragma: no cover - misconfigured app
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    return jobs


__all__ = [
    "MAX_QUEUED_MATCHES",
    "MatchJobs",
    "MatchStore",
    "get_match_jobs",
    "is_stale",
    "match_stats",
    "owns_video",
    "picture_path",
    "record_path",
    "video_objects",
    "run_match_analysis",
]
