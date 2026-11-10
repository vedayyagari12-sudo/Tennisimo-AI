"""``app/services/match_jobs.py`` with an in-memory store and a fake analyser."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.api.errors import ApiError
from app.models.court import CourtAnalysisResponse, CourtFormat
from app.models.enums import ErrorCode
from app.models.enums import FeedbackSource
from app.models.match import CreateMatchRequest, MatchRecord
from app.models.responses import CoachingFeedback, NumericGuardReport
from app.services import match_jobs as module
from app.services.match_jobs import MatchJobs, is_stale, match_stats, owns_video, record_path, utcnow
from app.services.storage import ObjectHead
from tests.unit.models.test_court_models import ANALYSIS_RESPONSE

BUCKET = "swing-videos"
USER = UUID("11111111-1111-1111-1111-111111111111")
VIDEO = f"{BUCKET}/{USER}/{uuid4()}.mp4"


def sample_result() -> CourtAnalysisResponse:
    return CourtAnalysisResponse.model_validate_json(ANALYSIS_RESPONSE)


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {VIDEO: b"video"}
        self.deleted: list[str] = []

    async def put_bytes(self, storage_path: str, body: bytes, content_type: str) -> None:
        self.objects[storage_path] = body

    async def get_bytes(self, storage_path: str) -> bytes | None:
        return self.objects.get(storage_path)

    async def list_names(self, folder_path: str) -> list[str]:
        prefix = folder_path.rstrip("/") + "/"
        return [k[len(prefix):] for k in self.objects if k.startswith(prefix) and "/" not in k[len(prefix):]]

    async def delete_object(self, storage_path: str) -> None:
        self.deleted.append(storage_path)
        self.objects.pop(storage_path, None)

    async def head_object(self, storage_path: str) -> ObjectHead:
        if storage_path not in self.objects:
            raise ApiError(ErrorCode.OBJECT_NOT_FOUND)
        return ObjectHead(size_bytes=len(self.objects[storage_path]), content_type="video/mp4")

    async def download_to_path(self, storage_path: str, destination: Path) -> int:
        destination.write_bytes(self.objects[storage_path])
        return len(self.objects[storage_path])


PICTURE = b"\xff\xd8jpeg"


def ok_analyzer(
    video: Path, request: CreateMatchRequest, progress: Callable[[float], None]
) -> tuple[CourtAnalysisResponse, str, float, bytes]:
    assert video.read_bytes() == b"video"
    progress(0.5)
    return sample_result(), "auto", 42.0, PICTURE


def fake_feedback(result: CourtAnalysisResponse) -> CoachingFeedback:
    return CoachingFeedback(
        summary=f"{result.summary.count} landings.",
        strengths=[],
        improvements=[],
        source=FeedbackSource.GEMINI,
        model="gemini-2.5-flash",
        guard=NumericGuardReport(passed=True, fell_back_to_template=False, mph_rule="banned"),
    )


def run_scenario(
    analyze: Any, body: Callable[[MatchJobs, MemoryStore], Any], coach: Any = fake_feedback
) -> Any:
    store = MemoryStore()

    async def main() -> Any:
        jobs = MatchJobs(
            store=store,  # type: ignore[arg-type]
            settings=SimpleNamespace(supabase_storage_bucket=BUCKET),  # type: ignore[arg-type]
            loop=asyncio.get_running_loop(),
            analyze=analyze,
            coach=coach,
        )
        try:
            return await body(jobs, store)
        finally:
            jobs.shutdown()

    return asyncio.run(main())


def test_a_match_runs_to_complete_and_its_video_is_deleted() -> None:
    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        record, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO, title="Sunday"))
        assert record.status == "queued"
        await asyncio.wrap_future(future)
        done = await jobs.get(USER, record.match_id)
        assert done is not None and done.status == "complete"
        assert done.result is not None and done.stats is not None
        assert done.calibration_source == "auto" and done.duration_s == 42.0
        assert done.title == "Sunday" and done.progress == 1.0
        assert done.has_court_picture
        assert await jobs.court_picture(USER, record.match_id) == PICTURE
        assert VIDEO in store.deleted and VIDEO not in store.objects
        listed = await jobs.list(USER)
        assert [m.match_id for m in listed] == [record.match_id]
        assert listed[0].stats == done.stats

    run_scenario(ok_analyzer, body)


def test_a_rally_gets_overall_feedback_and_a_match_does_not() -> None:
    coached: list[int] = []

    def coach(result: CourtAnalysisResponse) -> CoachingFeedback:
        coached.append(result.summary.count)
        return fake_feedback(result)

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        rally, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO, kind="rally"))
        await asyncio.wrap_future(future)
        done = await jobs.get(USER, rally.match_id)
        assert done is not None and done.status == "complete" and done.kind == "rally"
        assert done.feedback is not None and done.feedback.source is FeedbackSource.GEMINI
        assert VIDEO in store.deleted

        store.objects[VIDEO] = b"video"
        match, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO))
        await asyncio.wrap_future(future)
        other = await jobs.get(USER, match.match_id)
        assert other is not None and other.kind == "match" and other.feedback is None

        assert [m.match_id for m in await jobs.list(USER, "rally")] == [rally.match_id]
        assert [m.match_id for m in await jobs.list(USER, "match")] == [match.match_id]
        assert len(await jobs.list(USER)) == 2

    run_scenario(ok_analyzer, body, coach)
    assert len(coached) == 1


def test_a_coaching_failure_still_completes_the_rally() -> None:
    def broken(result: CourtAnalysisResponse) -> CoachingFeedback:
        raise RuntimeError("gemini down")

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        record, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO, kind="rally"))
        await asyncio.wrap_future(future)
        done = await jobs.get(USER, record.match_id)
        assert done is not None and done.status == "complete"
        assert done.result is not None and done.feedback is None

    run_scenario(ok_analyzer, body, broken)


def test_a_record_written_before_rallies_existed_reads_as_a_match() -> None:
    old = MatchRecord(
        match_id=uuid4(), status="complete", created_at=utcnow(), updated_at=utcnow(),
        court_format=CourtFormat.SINGLES,
    ).model_dump(mode="json")
    del old["kind"], old["feedback"]
    record = MatchRecord.model_validate(old)
    assert record.kind == "match" and record.feedback is None


def test_a_video_in_pieces_is_joined_byte_for_byte_and_every_piece_deleted() -> None:
    pieces = [b"first-", b"second-", b"third"]
    seen: list[bytes] = []

    def joined(video: Path, request: CreateMatchRequest, progress: Callable[[float], None]) -> Any:
        seen.append(video.read_bytes())
        return sample_result(), "auto", 1.0, None

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        del store.objects[VIDEO]
        for index, piece in enumerate(pieces):
            store.objects[f"{VIDEO}.part{index:03d}"] = piece
        record, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO, part_count=3))
        await asyncio.wrap_future(future)
        done = await jobs.get(USER, record.match_id)
        assert done is not None and done.status == "complete"
        assert not any(k.startswith(VIDEO) for k in store.objects)
        assert sorted(store.deleted) == [f"{VIDEO}.part{i:03d}" for i in range(3)]

    run_scenario(joined, body)
    assert seen == [b"first-second-third"]


def test_a_missing_piece_is_object_not_found() -> None:
    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        store.objects[f"{VIDEO}.part000"] = b"x"
        with pytest.raises(ApiError) as excinfo:
            await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO, part_count=2))
        assert excinfo.value.error_code is ErrorCode.OBJECT_NOT_FOUND

    run_scenario(ok_analyzer, body)


def test_a_failing_analysis_leaves_a_failed_record_and_still_deletes_the_video() -> None:
    def broken(*_args: Any) -> Any:
        raise RuntimeError("boom")

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        record, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO))
        await asyncio.wrap_future(future)
        failed = await jobs.get(USER, record.match_id)
        assert failed is not None and failed.status == "failed"
        assert failed.error_code is ErrorCode.INTERNAL_ERROR
        assert VIDEO in store.deleted

    run_scenario(broken, body)


def test_a_decode_error_keeps_its_own_code() -> None:
    from app.pose.video_io import VideoDecodeError

    def too_short(*_args: Any) -> Any:
        raise VideoDecodeError(ErrorCode.VIDEO_TOO_SHORT, "1.2s")

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        record, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO))
        await asyncio.wrap_future(future)
        failed = await jobs.get(USER, record.match_id)
        assert failed is not None and failed.error_code is ErrorCode.VIDEO_TOO_SHORT

    run_scenario(too_short, body)


@pytest.mark.parametrize(
    "path",
    [
        f"{BUCKET}/{uuid4()}/{uuid4()}.mp4",  # another user's folder
        f"{BUCKET}/{USER}/matches/{uuid4()}.json",  # a record, not a video
        f"{BUCKET}/{USER}/../x/{uuid4()}.mp4",
        f"other/{USER}/{uuid4()}.mp4",
    ],
)
def test_only_the_users_own_uploaded_video_is_accepted(path: str) -> None:
    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        with pytest.raises(ApiError) as excinfo:
            await jobs.create(USER, CreateMatchRequest(storage_path=path))
        assert excinfo.value.error_code is ErrorCode.STORAGE_PATH_FORBIDDEN

    run_scenario(ok_analyzer, body)
    assert owns_video(VIDEO, BUCKET, USER)


def test_a_missing_video_is_object_not_found() -> None:
    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        with pytest.raises(ApiError) as excinfo:
            await jobs.create(USER, CreateMatchRequest(storage_path=f"{BUCKET}/{USER}/{uuid4()}.mp4"))
        assert excinfo.value.error_code is ErrorCode.OBJECT_NOT_FOUND

    run_scenario(ok_analyzer, body)


def test_a_full_queue_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "MAX_QUEUED_MATCHES", 0)

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        with pytest.raises(ApiError) as excinfo:
            await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO))
        assert excinfo.value.error_code is ErrorCode.QUEUE_FULL

    run_scenario(ok_analyzer, body)


def test_a_record_left_running_by_a_lost_worker_reads_as_failed() -> None:
    match_id = uuid4()
    old = utcnow() - timedelta(minutes=10)
    stuck = MatchRecord(
        match_id=match_id, status="running", created_at=old, updated_at=old,
        court_format=CourtFormat.SINGLES, progress=0.4,
    )

    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        store.objects[record_path(BUCKET, USER, match_id)] = stuck.model_dump_json().encode()
        lost = await jobs.get(USER, match_id)
        assert lost is not None and lost.status == "failed"
        assert lost.error_code is ErrorCode.WORKER_LOST
        stored = MatchRecord.model_validate_json(store.objects[record_path(BUCKET, USER, match_id)])
        assert stored.status == "failed"

    run_scenario(ok_analyzer, body)
    assert not is_stale(stuck, now=utcnow(), active=True)


def test_delete_removes_the_record() -> None:
    async def body(jobs: MatchJobs, store: MemoryStore) -> None:
        record, future = await jobs.create(USER, CreateMatchRequest(storage_path=VIDEO))
        await asyncio.wrap_future(future)
        assert await jobs.delete(USER, record.match_id)
        assert await jobs.get(USER, record.match_id) is None
        assert await jobs.court_picture(USER, record.match_id) is None
        assert not await jobs.delete(USER, record.match_id)

    run_scenario(ok_analyzer, body)


def test_match_stats_counts_calls_and_rallies() -> None:
    result = sample_result()
    stats = match_stats(result)
    assert stats.shots_landed == len(result.landings)
    assert stats.rallies == len(result.rallies)
    called = [l for l in result.landings if l.line_call is not None]
    assert stats.in_count + stats.out_count <= len(called)
    if stats.in_count + stats.out_count == 0:
        assert stats.in_percent is None
    empty = result.model_copy(update={"landings": [], "rallies": []})
    blank = match_stats(empty)
    assert blank.shots_landed == 0 and blank.longest_rally_hits is None
    assert blank.in_percent is None and blank.deep_percent is None
