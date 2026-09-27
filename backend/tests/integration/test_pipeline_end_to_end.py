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
import dataclasses
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

import numpy as np
import pytest

from app.analysis.normalize import normalize_sequence
from app.config import Settings
from app.models.enums import (
    AnalysisStatus,
    BallSpeedUnavailableReason,
    ErrorCode,
    FeedbackSource,
    Handedness,
    JobStatus,
)
from app.models.internal import PoseSequence
from app.models.requests import CreateAnalysisRequest
from app.models.responses import AnalysisResponse
from app.services import pipeline as pipeline_module
from app.services.orchestrator import JobContext, JobFailure, JobOrchestrator
from app.services.pipeline import run_analysis_job
from app.services.repository import AnalysisRow

CLIP = Path(__file__).resolve().parents[1] / "fixtures" / "clips" / "serve_vertical_10340710.mp4"

#: README.md: contact at source frames 86-88 of a 25 fps clip.
GROUND_TRUTH_CONTACT_S: tuple[float, float] = (86.0 / 25.0, 88.0 / 25.0)

#: The player in the vendored clip serves RIGHT-handed. The hint is sent because
#: PIPELINE.md Stage 8 says the client always sends one from the user profile,
#: and 9.2.8 makes a present, correct hint a PRECONDITION of interpreting Stage
#: 9 at all: the candidate list is computed on the racket-hand speed curve, so a
#: wrong hand changes every candidate.
#:
#: This is not cosmetic and it is not a convenience. Stage 8's own
#: discrimination on this clip is a COIN FLIP: hint-free it returns ``left``
#: (confidence 0.452) under these settings and ``right`` (0.447) when the
#: analysis window moves by a single frame, and 9.1 already records it choosing
#: the wrong hand on more than half of a 7-clip batch. With Stage 9 able to
#: refuse (``contact_not_found``), a hint-free run of this clip now fails the
#: JOB rather than quietly analysing the non-racket wrist. That is Stage 8's
#: open defect surfacing, not Stage 9's -- see PIPELINE.md 9.2.9.
CLIP_HANDEDNESS: Handedness = Handedness.RIGHT

#: +-2 source frames at 25 fps. Wider than Stage 9's documented +1 residual so
#: the band is not itself the thing under test.
CONTACT_TOLERANCE_S: float = 2.0 / 25.0

#: NOTHING about Stage 7 is relaxed here any more. Until Defect 2 (PIPELINE.md
#: 7.1) this module ran with ``max_gap_frames=8`` because the whole-window gap
#: gate rejected this clip outright: the player is untrackable before ~2.2 s and
#: after ~4.7 s, which is ordinary single-camera footage, not bad footage. The
#: two-tier verdict judges a 2 s core instead, this clip passes at STOCK
#: settings, and the relaxation is gone. If it ever has to come back, that is a
#: regression in the gate, not a test fixture detail.

#: Cap on sampled frames, set through Settings like any operator would.
SAMPLED_FRAME_CAP: int = 80

#: Analysis window for the two real runs, also set through Settings. Stage 5 now
#: centres the window on the strike (measured 3.38 s against a ground truth of
#: 3.44-3.52 s), so narrowing the window keeps it CENTRED there: 2.67 s is
#: exactly ``SAMPLED_FRAME_CAP`` frames at 30 fps and covers roughly
#: [2.05, 4.71] s -- the span over which this clip holds a trackable pose.
#:
#: Before Defect 1 (PIPELINE.md 5.1) was fixed, the same span was reached BY
#: ACCIDENT: the scan returned its winning bucket's end, 6.08 s, the 8 s window
#: clamped to [2.08, 10.08], and the 80-frame cap truncated it from the start.
#: The cap can only truncate a window's TAIL, so with a correctly centred window
#: it no longer doubles as a window-narrowing knob. Stating the width is the
#: honest version of what that accident was doing.
RUN_ANALYSIS_WINDOW_S: float = SAMPLED_FRAME_CAP / 30.0

FAKE_ENV: dict[str, str] = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
    "MAX_ANALYSIS_FRAMES": str(SAMPLED_FRAME_CAP),
}

#: What the two REAL runs use. Kept separate from ``FAKE_ENV`` so that
#: ``test_stock_settings_now_reach_an_analysis`` keeps testing stock Stage 5
#: settings.
RUN_ENV: dict[str, str] = {
    **FAKE_ENV,
    "ANALYSIS_WINDOW_SECONDS": f"{RUN_ANALYSIS_WINDOW_S:.4f}",
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
        handedness_hint=CLIP_HANDEDNESS,
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
    settings = Settings(env=RUN_ENV)
    client = ReplayGeminiClient()

    context = make_context(repository, storage, settings)
    run_analysis_job(context, gemini_client=client)

    assert repository.rows, "the runner persisted nothing"
    yield {
        "repository": repository,
        "storage": storage,
        "client": client,
        "row": repository.rows[0],
        "response": AnalysisResponse.model_validate(repository.rows[0].payload),
        "context": context,
    }


def test_stock_settings_now_reach_an_analysis() -> None:
    """The former DEFECT PIN, inverted (PIPELINE.md 7.1).

    This test used to assert that the vendored clip produced NO analysis at all
    at stock settings: the player is not trackable before ~2.2 s or after
    ~4.7 s of an 8 s window, and the whole-window 3-frame gap bound rejected
    the job with ``pose_quality_too_low``. Under the two-tier verdict the 2 s
    core around the swing is fully tracked, the clip is usable, and the
    98-frame hole is recorded as a ``dead_time_outside_core`` flag instead of a
    failure. The honest-rejection path it used to cover is now covered by
    ``test_a_hole_through_contact_is_still_rejected`` below, on a clip whose
    tracking really is broken where it matters.
    """
    repository = RecordingRepository()
    settings = Settings(env={k: v for k, v in FAKE_ENV.items() if k != "MAX_ANALYSIS_FRAMES"})
    context = make_context(repository, ClipStorage(), settings)

    run_analysis_job(context, gemini_client=ReplayGeminiClient())

    assert repository.rows, "the runner persisted nothing"
    response = AnalysisResponse.model_validate(repository.rows[0].payload)
    quality = response.pose_quality
    assert quality.usable is True
    assert quality.longest_gap_frames > 3  # the old gate rejected on exactly this
    assert "dead_time_outside_core" in quality.flags
    assert quality.core_coverage_fraction >= 0.9
    assert quality.longest_core_gap_frames <= 3


def test_a_hole_through_contact_is_still_rejected() -> None:
    """The gate keeps its teeth: a clip is rejected when the SWING is missing.

    Real decode, real MediaPipe, real Stage 7 -- with one defect injected into
    the seam: every detection is dropped for ~0.3 s straight through the
    ground-truth contact at 3.44-3.52 s. That is a hole no interpolation may
    fill, inside any core window the anchor can place, and the job must fail
    cleanly with ``pose_quality_too_low`` rather than measuring a swing it
    cannot see. A gate that passes everything is not a fix, it is a deletion.
    """
    repository = RecordingRepository()
    settings = Settings(env={k: v for k, v in FAKE_ENV.items() if k != "MAX_ANALYSIS_FRAMES"})
    context = make_context(repository, ClipStorage(), settings)

    def blind_the_contact(seq: PoseSequence, **kwargs: Any) -> Any:
        hole = (seq.timestamps_s >= 3.30) & (seq.timestamps_s <= 3.60)
        landmarks = np.array(seq.landmarks, copy=True)
        landmarks[hole] = 0.0
        detected = np.array(seq.detected, copy=True)
        detected[hole] = False
        return normalize_sequence(
            dataclasses.replace(seq, landmarks=landmarks, detected=detected), **kwargs
        )

    original = pipeline_module.normalize_sequence
    pipeline_module.normalize_sequence = blind_the_contact  # type: ignore[assignment]
    try:
        with pytest.raises(JobFailure) as excinfo:
            run_analysis_job(context, gemini_client=ReplayGeminiClient())
    finally:
        pipeline_module.normalize_sequence = original  # type: ignore[assignment]

    assert excinfo.value.error_code is ErrorCode.POSE_QUALITY_TOO_LOW
    assert excinfo.value.stage == "normalize"
    assert not repository.rows


def test_run_persists_a_validatable_analysis(completed_run: dict[str, Any]) -> None:
    row: AnalysisRow = completed_run["row"]
    response: AnalysisResponse = completed_run["response"]

    assert row.id == response.analysis_id
    assert row.pipeline_version == "v3"
    assert row.rubric_version == response.scorecard.rubric_version
    assert row.shot_type is response.shot_type.shot_type
    assert row.overall_score == response.scorecard.overall_score
    assert row.ball_speed_mph is response.ball_speed.ball_speed_mph
    # REGRESSION: the row must carry the pairing partner of ball_speed_mph, and
    # must still carry it through the exclude_none dump persist_success sends.
    assert row.ball_speed_unavailable_reason is response.ball_speed.unavailable_reason
    body = row.model_dump(mode="json", exclude_none=True)
    assert ("ball_speed_mph" in body) != ("ball_speed_unavailable_reason" in body)
    assert response.status in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL)


def test_stage_5_window_is_absolute_and_covers_the_strike(
    completed_run: dict[str, Any],
) -> None:
    """The Stage 5/6 seam did not pre-subtract the window start, AND the window
    is CENTRED on the strike rather than merely containing it.

    ``contact_absolute_time_s`` is an absolute PTS in the source file, so it
    must be at or after ``analysis_window_start_s``. If the generator had
    yielded window-relative timestamps, ``extract_keypoints`` would have
    subtracted the start a second time and this would sit near zero.

    The centring assertion is the one that would have failed before Defect 1
    (PIPELINE.md 5.1) was fixed: "covers the strike" passed happily with the
    window centre 2.6 s past it, because the clamp at the clip end dragged the
    window back far enough to keep the strike inside. That was luck, and a test
    that only asserts coverage leaves the hole open.
    """
    response: AnalysisResponse = completed_run["response"]
    video = response.video

    assert video.analysis_window_start_s > 0.0, "the motion scan should have moved the window"
    strike_s = sum(GROUND_TRUTH_CONTACT_S) / 2.0
    window_centre_s = (video.analysis_window_start_s + video.analysis_window_end_s) / 2.0
    assert abs(window_centre_s - strike_s) <= 1.0, (
        f"window centred at {window_centre_s:.2f}s, strike at {strike_s:.2f}s"
    )
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
    # A regression into blanket rejection is caught by the same assertion that
    # catches the degenerate return (PIPELINE.md 9.2.7).
    assert "contact_not_found" not in contact.sanity_flags


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN DEFECT, logged in docs/PIPELINE.md 9.2: on this clip Stage 9 selects a "
        "contact about 0.6 s (15 source frames) before the ground-truth 86-88, far "
        "outside the documented +1 frame residual. The same frame is selected whether "
        "the sequence comes through Stage 5's 30 fps window or a naive native-rate "
        "decode, so the miss is Stage 9's and not the seam's. "
        "STILL FAILING AFTER Defect 3 (9.2.9), and 9.2 predicted that it would: "
        "candidate filtering removes an implausible WINNER, it does not make wrist "
        "speed a good estimator of racket-head timing on a serve (root cause (b), "
        "untouched). At these settings the arm-drive candidate passes both filter "
        "gates, so the answer is unchanged at source frame 71. Worse, the candidate "
        "whose plateau walk lands on source frame 87 -- exactly the ground truth -- IS "
        "enumerated and is REJECTED by wrist_behind_mid_hip, because on a serve the "
        "wrist crosses mid-hip within a frame of contact. Fixing root cause (b) "
        "without revisiting that gate would swap this miss for a refusal."
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
    try:
        orchestrator = JobOrchestrator(
            repository=repository,  # type: ignore[arg-type]
            storage=ClipStorage(),  # type: ignore[arg-type]
            settings=Settings(env=RUN_ENV),
            loop=loop,
            runner=partial(run_analysis_job, gemini_client=client),
        )
        job_id = uuid4()
        future = orchestrator.submit(
            job_id=job_id,
            user_id=uuid4(),
            storage_path=f"swing-videos/{job_id}/clip.mp4",
            request=CreateAnalysisRequest(
                storage_path=f"swing-videos/{job_id}/clip.mp4",
                handedness_hint=CLIP_HANDEDNESS,
            ),
        )
        future.result(timeout=300)
        orchestrator.shutdown()
    finally:
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
