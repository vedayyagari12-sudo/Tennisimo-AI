"""End-to-end: the real Stage 4-18 runner against the vendored real clip.

SLOW. Runs actual MediaPipe inference and a real PyAV decode, so every test here
carries ``@pytest.mark.slow`` and can be deselected with ``-m "not slow"``.

NO NETWORK BY DEFAULT. Gemini is reached only through the ``GeminiClient``
Protocol, and the default client here is :class:`ReplayGeminiClient`, which
returns a fixture string. The opt-in live test at the bottom is marked
``gemini_live`` and is skipped unless ``GEMINI_API_KEY`` is present in the
process environment. The key lives in ``backend/.env`` (gitignored) and is never
read from a file, never printed and never written into this test.

GROUND TRUTH (``tests/fixtures/clips/README.md``): ball visible at source frame
84, contact at source frames 86-88, 25 fps, so contact is 3.44-3.52 s of
absolute PTS. Stage 9 carries an accepted +1 frame residual, so the assertion
below is a tolerance BAND, never an exact frame.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.analysis.normalize import normalize_sequence
from app.config import Settings
from app.models.enums import (
    AnalysisStatus,
    BallSpeedUnavailableReason,
    ErrorCode,
    FeedbackSource,
    JobStatus,
)
from app.models.requests import CreateAnalysisRequest
from app.models.responses import AnalysisResponse
from app.services import pipeline as pipeline_module
from app.services.orchestrator import JobContext, JobFailure, JobOrchestrator
from app.services.pipeline import run_analysis_job
from app.services.repository import AnalysisRow

CLIP = Path(__file__).resolve().parents[1] / "fixtures" / "clips" / "serve_vertical_10340710.mp4"

#: README.md: contact at source frames 86-88 of a 25 fps clip.
GROUND_TRUTH_CONTACT_S: tuple[float, float] = (86.0 / 25.0, 88.0 / 25.0)

#: +-2 source frames at 25 fps. Wider than Stage 9's documented +1 residual so
#: the band is not itself the thing under test.
CONTACT_TOLERANCE_S: float = 2.0 / 25.0

#: Stage 7's default ``max_gap_frames`` is 3 (``smoothing.py:16``). On this clip
#: MediaPipe's core-landmark visibility dips below the 0.5 gate for 4
#: consecutive frames, which fails the whole job (see
#: ``test_default_settings_fail_on_pose_quality``). The pure function takes the
#: bound as a keyword argument, so the run below relaxes exactly that one number
#: in order to exercise Stages 8-18 on real footage. Nothing else is patched.
RELAXED_MAX_GAP_FRAMES: int = 8

#: Cap on sampled frames, set through Settings like any operator would. The
#: motion scan places the window at 2.08 s; 80 frames at 30 fps covers
#: [2.08, 4.72] s, which contains the ground-truth contact at ~3.44 s and
#: excludes the tail of the clip where the player is no longer trackable.
SAMPLED_FRAME_CAP: int = 80

FAKE_ENV: dict[str, str] = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
    "MAX_ANALYSIS_FRAMES": str(SAMPLED_FRAME_CAP),
}

#: A fixture Gemini response. Deliberately number-free: the numeric guard
#: rejects any figure that is not in the payload, and a canned reply cannot know
#: this clip's numbers.
GEMINI_FIXTURE_RESPONSE: str = (
    '{"summary": "A committed serve motion with a clear upward swing path.",'
    ' "strengths": ["The head stays quiet through the strike."],'
    ' "improvements": [{"priority": 1, "title": "Hold the shoulder turn longer",'
    ' "why": "The upper body opens before the arm arrives.",'
    ' "cue": "Chin on the shoulder until the arm goes.",'
    ' "drill": "Shadow serves pausing at the trophy position.",'
    ' "metric_refs": ["shoulder_turn_deg"]}]}'
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CLIP.is_file(), reason=f"vendored clip missing: {CLIP}"),
]


class ReplayGeminiClient:
    """Fixture replay through the ``GeminiClient`` Protocol. Never a network call."""

    def __init__(self, response: str = GEMINI_FIXTURE_RESPONSE) -> None:
        self.response = response
        self.calls: list[str] = []

    def generate(
        self, *, system_instruction: str, prompt: str, config: dict[str, Any]
    ) -> str:
        self.calls.append(prompt)
        return self.response


class ClipStorage:
    """Stage 4 stand-in: copies the vendored clip instead of fetching it."""

    def __init__(self) -> None:
        self.downloads: list[str] = []

    async def download_to_path(self, storage_path: str, destination: Path) -> int:
        self.downloads.append(storage_path)
        shutil.copyfile(CLIP, destination)
        return destination.stat().st_size


@dataclass
class RecordingRepository:
    """In-memory Stage 18 sink plus a heartbeat counter."""

    rows: list[AnalysisRow] = field(default_factory=list)
    heartbeats: int = 0
    running: list[UUID] = field(default_factory=list)
    failures: list[tuple[ErrorCode, str]] = field(default_factory=list)

    async def mark_running(self, job_id: UUID, at: datetime) -> None:
        self.running.append(job_id)

    async def heartbeat(self, job_id: UUID, at: datetime) -> None:
        self.heartbeats += 1

    async def mark_failed(
        self, job_id: UUID, error_code: ErrorCode, message: str, stage: str
    ) -> None:
        self.failures.append((error_code, stage))

    async def persist_success(self, analysis: AnalysisRow) -> None:
        self.rows.append(analysis)


def make_context(
    repository: RecordingRepository,
    storage: ClipStorage,
    settings: Settings,
    *,
    calibration: Any | None = None,
) -> JobContext:
    """A JobContext whose ``run_async`` runs the coroutine on a throwaway loop."""
    job_id = uuid4()
    request = CreateAnalysisRequest(
        storage_path=f"swing-videos/{job_id}/clip.mp4",
        ball_speed_calibration=calibration,
    )
    return JobContext(
        job_id=job_id,
        user_id=uuid4(),
        storage_path=request.storage_path,
        request=request,
        repository=repository,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        settings=settings,
        heartbeat=lambda: asyncio.run(repository.heartbeat(job_id, datetime.now())),
        run_async=lambda coro: asyncio.run(coro),
    )


@pytest.fixture(scope="module")
def completed_run() -> Iterator[dict[str, Any]]:
    """Run the pipeline ONCE for the whole module. Real MediaPipe is not cheap."""
    repository = RecordingRepository()
    storage = ClipStorage()
    settings = Settings(env=FAKE_ENV)
    client = ReplayGeminiClient()

    original = pipeline_module.normalize_sequence
    pipeline_module.normalize_sequence = partial(  # type: ignore[assignment]
        normalize_sequence, max_gap_frames=RELAXED_MAX_GAP_FRAMES
    )
    try:
        context = make_context(repository, storage, settings)
        run_analysis_job(context, gemini_client=client)
    finally:
        pipeline_module.normalize_sequence = original  # type: ignore[assignment]

    assert repository.rows, "the runner persisted nothing"
    yield {
        "repository": repository,
        "storage": storage,
        "client": client,
        "row": repository.rows[0],
        "response": AnalysisResponse.model_validate(repository.rows[0].payload),
        "context": context,
    }


def test_default_settings_fail_on_pose_quality() -> None:
    """DEFECT PIN, not an aspiration.

    With stock settings the vendored clip does not produce an analysis at all:
    the motion scan centres an 8 s window at 2.08 s, the player stops being
    trackable at about 4.85 s, and the resulting 156-frame gap trips Stage 7's
    3-frame ``max_gap_frames`` bound. The job fails CLEANLY -- a ``JobFailure``
    carrying ``pose_quality_too_low`` -- rather than crashing, and that clean
    failure is what this test pins. Change it when the gate or the window
    placement changes, not by loosening the assertion.
    """
    repository = RecordingRepository()
    settings = Settings(env={k: v for k, v in FAKE_ENV.items() if k != "MAX_ANALYSIS_FRAMES"})
    context = make_context(repository, ClipStorage(), settings)

    with pytest.raises(JobFailure) as excinfo:
        run_analysis_job(context, gemini_client=ReplayGeminiClient())

    assert excinfo.value.error_code is ErrorCode.POSE_QUALITY_TOO_LOW
    assert excinfo.value.stage == "normalize"
    assert not repository.rows


def test_run_persists_a_validatable_analysis(completed_run: dict[str, Any]) -> None:
    row: AnalysisRow = completed_run["row"]
    response: AnalysisResponse = completed_run["response"]

    assert row.id == response.analysis_id
    assert row.pipeline_version == "v2"
    assert row.rubric_version == response.scorecard.rubric_version
    assert row.shot_type is response.shot_type.shot_type
    assert row.overall_score == response.scorecard.overall_score
    assert row.ball_speed_mph is response.ball_speed.ball_speed_mph
    assert response.status in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL)


def test_stage_5_window_is_absolute_and_covers_the_strike(
    completed_run: dict[str, Any],
) -> None:
    """The Stage 5/6 seam did not pre-subtract the window start.

    ``contact_absolute_time_s`` is an absolute PTS in the source file, so it
    must be at or after ``analysis_window_start_s``. If the generator had
    yielded window-relative timestamps, ``extract_keypoints`` would have
    subtracted the start a second time and this would sit near zero.
    """
    response: AnalysisResponse = completed_run["response"]
    video = response.video

    assert video.analysis_window_start_s > 0.0, "the motion scan should have moved the window"
    assert video.frames_sampled == SAMPLED_FRAME_CAP
    assert response.contact.contact_absolute_time_s >= video.analysis_window_start_s
    assert response.contact.contact_absolute_time_s <= video.analysis_window_end_s
    assert response.contact.contact_absolute_time_s == pytest.approx(
        video.analysis_window_start_s + response.contact.time_s
    )


def test_contact_is_reported_with_its_confidence_and_flags(
    completed_run: dict[str, Any],
) -> None:
    """Stage 9 ran and produced a frame inside the sampled sequence."""
    response: AnalysisResponse = completed_run["response"]
    contact = response.contact

    assert 0 <= contact.frame_index < SAMPLED_FRAME_CAP
    assert 0.0 <= contact.confidence <= 1.0
    assert "sequence_unusable" not in contact.sanity_flags


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN DEFECT, logged in docs/PIPELINE.md: on this clip Stage 9 selects a "
        "contact about 0.6 s (15 source frames) before the ground-truth 86-88, far "
        "outside the documented +1 frame residual. The same frame is selected whether "
        "the sequence comes through Stage 5's 30 fps window or a naive native-rate "
        "decode, so the miss is Stage 9's and not the seam's."
    ),
)
def test_contact_matches_the_ground_truth_band(completed_run: dict[str, Any]) -> None:
    response: AnalysisResponse = completed_run["response"]
    earliest = GROUND_TRUTH_CONTACT_S[0] - CONTACT_TOLERANCE_S
    latest = GROUND_TRUTH_CONTACT_S[1] + CONTACT_TOLERANCE_S
    assert earliest <= response.contact.contact_absolute_time_s <= latest


def test_shot_type_and_scorecard_are_produced(completed_run: dict[str, Any]) -> None:
    response: AnalysisResponse = completed_run["response"]

    assert 0.0 <= response.shot_type.confidence <= 1.0
    assert response.shot_type.evidence, "Stage 14 must explain itself"
    assert len(response.scorecard.categories) == 5
    # A category with no measurable metric scores None, never 0.0.
    for category in response.scorecard.categories:
        if category.metrics_available == 0:
            assert category.score_0_100 is None


def test_ball_speed_is_skipped_without_calibration(completed_run: dict[str, Any]) -> None:
    """No calibration => Stages 10 and 11 never ran, and the job did not fail."""
    response: AnalysisResponse = completed_run["response"]

    assert response.ball_speed.ball_speed_mph is None
    assert (
        response.ball_speed.unavailable_reason
        is BallSpeedUnavailableReason.NOT_CALIBRATED
    )
    assert response.ball_speed.detections_used == 0
    assert response.timings.ball_detect_ms == 0
    assert response.timings.ball_speed_ms == 0


def test_no_network_call_was_made(completed_run: dict[str, Any]) -> None:
    """The default suite burns no Gemini credits.

    On this clip Stage 17 skips the model entirely because contact confidence is
    below ``MIN_CONTACT_CONFIDENCE_FOR_GEMINI``; the assertion is therefore that
    the client was reached AT MOST once and, whichever path ran, through the
    Protocol rather than the SDK.
    """
    client: ReplayGeminiClient = completed_run["client"]
    response: AnalysisResponse = completed_run["response"]

    assert len(client.calls) <= 1
    if not client.calls:
        assert response.feedback.source is FeedbackSource.TEMPLATE
    else:
        assert response.feedback.source in (
            FeedbackSource.GEMINI,
            FeedbackSource.GEMINI_PARTIAL,
            FeedbackSource.TEMPLATE,
        )


def test_heartbeats_were_written_at_stage_boundaries(
    completed_run: dict[str, Any],
) -> None:
    repository: RecordingRepository = completed_run["repository"]
    # download, decode, pose, analysis, ball, feedback.
    assert repository.heartbeats >= 6


def test_timings_are_recorded(completed_run: dict[str, Any]) -> None:
    response: AnalysisResponse = completed_run["response"]
    timings = response.timings

    assert timings.pose_ms > 0
    assert timings.decode_ms > 0
    assert timings.total_ms >= timings.pose_ms


def test_orchestrator_drives_the_real_runner_to_succeeded() -> None:
    """The same run again, but through ``JobOrchestrator``.

    This is what proves the runner's synchronous body can reach the async
    repository at all: every coroutine goes over ``run_async`` onto a loop
    running in a different thread.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    repository = RecordingRepository()
    client = ReplayGeminiClient()
    original = pipeline_module.normalize_sequence
    pipeline_module.normalize_sequence = partial(  # type: ignore[assignment]
        normalize_sequence, max_gap_frames=RELAXED_MAX_GAP_FRAMES
    )
    try:
        orchestrator = JobOrchestrator(
            repository=repository,  # type: ignore[arg-type]
            storage=ClipStorage(),  # type: ignore[arg-type]
            settings=Settings(env=FAKE_ENV),
            loop=loop,
            runner=partial(run_analysis_job, gemini_client=client),
        )
        job_id = uuid4()
        future = orchestrator.submit(
            job_id=job_id,
            user_id=uuid4(),
            storage_path=f"swing-videos/{job_id}/clip.mp4",
            request=CreateAnalysisRequest(storage_path=f"swing-videos/{job_id}/clip.mp4"),
        )
        future.result(timeout=300)
        orchestrator.shutdown()
    finally:
        pipeline_module.normalize_sequence = original  # type: ignore[assignment]
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()

    assert repository.running == [job_id]
    assert not repository.failures
    assert [row.id for row in repository.rows] == [job_id]
    assert repository.rows[0].status in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL)


@pytest.mark.gemini_live
@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY is not in the environment; the live test is opt-in",
)
def test_live_gemini_call(completed_run: dict[str, Any]) -> None:
    """OPT-IN. Calls the real API with the real numbers from this clip.

    Costs credits. Runs only when ``GEMINI_API_KEY`` is exported; the key is
    read from the environment, never from a file, and never printed.
    """
    from app.feedback.gemini import (
        GoogleGenAIClient,
        generate_feedback,
        should_skip_gemini,
    )
    from app.feedback.projection import build_feedback_input

    response: AnalysisResponse = completed_run["response"]
    core = build_feedback_input(
        scorecard=response.scorecard,
        shot_type=response.shot_type,
        handedness=response.handedness.handedness.value,
        contact_confidence=response.contact.confidence,
        ball_speed=response.ball_speed,
    )
    if should_skip_gemini(core):
        pytest.skip(
            "this clip's contact confidence gates Stage 17, so there is no live "
            "call to make; the skip is the pipeline working as specified"
        )

    feedback = generate_feedback(core, GoogleGenAIClient())
    assert feedback.summary
    assert feedback.source in (
        FeedbackSource.GEMINI,
        FeedbackSource.GEMINI_PARTIAL,
        FeedbackSource.TEMPLATE,
    )


def test_job_status_enum_is_untouched_by_this_module() -> None:
    """Guard: the runner reports ``AnalysisStatus``, never ``JobStatus``."""
    response_status = {status.value for status in AnalysisStatus}
    assert response_status.isdisjoint({status.value for status in JobStatus})
