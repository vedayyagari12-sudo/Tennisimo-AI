"""Unit tests for the Stage 4-18 runner's own logic.

Everything here is fast: no video, no MediaPipe, no network. The end-to-end run
on a real clip lives in ``tests/integration/test_pipeline_end_to_end.py`` and is
marked ``slow``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.config import Settings
from app.models.enums import (
    BallSpeedUnavailableReason,
    ErrorCode,
    FeedbackSource,
    Handedness,
    HandednessSource,
)
from app.models.feedback import CoachingFeedback as FeedbackCoachingFeedback
from app.models.feedback import Improvement as FeedbackImprovement
from app.models.feedback import NumericGuardReport as FeedbackGuard
from app.models.requests import CreateAnalysisRequest
from app.models.responses import ContactDetection, HandednessResult, PoseQuality, VideoMeta
from app.services.orchestrator import JobContext, JobFailure
from app.services.pipeline import (
    TRUNCATION_MARGIN_FRAMES,
    Accumulator,
    StageTimer,
    ball_speed_stage,
    contact_at_window_edge,
    stage_timings,
    to_wire_feedback,
    unavailable_speed,
)

FAKE_ENV: dict[str, str] = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}


def make_contact(frame_index: int = 10) -> ContactDetection:
    return ContactDetection(
        frame_index=frame_index,
        peak_frame_index=frame_index,
        prominence_ratio=1.8,
        time_s=frame_index / 30.0,
        contact_absolute_time_s=2.0 + frame_index / 30.0,
        confidence=0.6,
        peak_hand_speed_tu_s=8.0,
        sanity_flags=[],
    )


def make_video() -> VideoMeta:
    return VideoMeta(
        duration_s=12.0,
        source_fps=25.0,
        analysis_fps=30.0,
        rotation_deg=0,
        width_px=360,
        height_px=640,
        cal_space_width_px=720,
        cal_space_height_px=1280,
        frames_decoded=300,
        frames_sampled=240,
        analysis_window_start_s=2.0,
        analysis_window_end_s=10.0,
        motion_scan_used=True,
    )


def make_quality() -> PoseQuality:
    return PoseQuality(
        frames_with_pose=200,
        frames_missing=40,
        detection_rate=0.83,
        mean_visibility=0.8,
        longest_gap_frames=2,
        interpolated_frames=40,
        torso_scale_px=120.0,
        usable=True,
    )


def make_context(
    *,
    settings: Settings | None = None,
    storage: Any | None = None,
    repository: Any | None = None,
    calibration: Any | None = None,
) -> JobContext:
    job_id = uuid4()
    return JobContext(
        job_id=job_id,
        user_id=uuid4(),
        storage_path=f"swing-videos/{job_id}/clip.mp4",
        request=CreateAnalysisRequest(
            storage_path=f"swing-videos/{job_id}/clip.mp4",
            ball_speed_calibration=calibration,
        ),
        repository=repository,
        storage=storage,
        settings=settings if settings is not None else Settings(env=FAKE_ENV),
        heartbeat=lambda: None,
        run_async=lambda coro: asyncio.run(coro),
    )


# --------------------------------------------------------------------------- #
# Ball speed never fails the job
# --------------------------------------------------------------------------- #


def test_absent_calibration_skips_stages_10_and_11_entirely() -> None:
    """No second decode pass, no detector, no timing entry. Just a reason."""
    timer = StageTimer()
    accumulator = Accumulator()

    result = ball_speed_stage(
        clip=Path("does-not-exist.mp4"),
        context=make_context(),
        seq=None,  # type: ignore[arg-type] - never touched on this path
        quality=make_quality(),
        handedness=HandednessResult(
            handedness=Handedness.RIGHT, confidence=0.8, source=HandednessSource.DETECTED
        ),
        contact=make_contact(),
        video=make_video(),
        timer=timer,
        accumulator=accumulator,
    )

    assert result.ball_speed_mph is None
    assert result.unavailable_reason is BallSpeedUnavailableReason.NOT_CALIBRATED
    assert timer.ms("ball_detect") == 0
    assert accumulator.warnings == [], "an uncalibrated clip is not a fault"
    assert accumulator.partial is False


def test_unknown_handedness_skips_the_ball_stage_instead_of_raising() -> None:
    """REGRESSION GUARD.

    ``racket_wrist_index`` RAISES on ``Handedness.UNKNOWN`` rather than
    defaulting to the right wrist. Calling it unguarded here would propagate out
    of the runner and fail the whole job over a ball-speed detail, which
    PIPELINE.md:594 forbids.
    """
    from app.models.requests import BallSpeedCalibration, NormalizedPoint
    from app.models.enums import CourtReference

    calibration = BallSpeedCalibration(
        point_a=NormalizedPoint(x=0.2, y=0.3),
        point_b=NormalizedPoint(x=0.8, y=0.75),
        reference=CourtReference.SIDELINE_BASELINE_TO_NET,
        distance_m=11.885,
        capture_width_px=1080,
        capture_height_px=1920,
    )
    accumulator = Accumulator()

    result = ball_speed_stage(
        clip=Path("does-not-exist.mp4"),
        context=make_context(calibration=calibration),
        seq=None,  # type: ignore[arg-type]
        quality=make_quality(),
        handedness=HandednessResult(
            handedness=Handedness.UNKNOWN,
            confidence=0.1,
            source=HandednessSource.DETECTED,
        ),
        contact=make_contact(),
        video=make_video(),
        timer=StageTimer(),
        accumulator=accumulator,
    )

    assert result.ball_speed_mph is None
    assert result.unavailable_reason is BallSpeedUnavailableReason.NO_TRACK_SEEDED
    assert accumulator.warnings, "the skip must say why"
    assert accumulator.partial is False, "ball speed never downgrades the analysis"


def test_detection_disabled_is_its_own_reason() -> None:
    from app.models.requests import BallSpeedCalibration, NormalizedPoint
    from app.models.enums import CourtReference

    settings = Settings(env={**FAKE_ENV, "BALL_DETECTION_ENABLED": "false"})
    calibration = BallSpeedCalibration(
        point_a=NormalizedPoint(x=0.2, y=0.3),
        point_b=NormalizedPoint(x=0.8, y=0.75),
        reference=CourtReference.SIDELINE_BASELINE_TO_NET,
        distance_m=11.885,
        capture_width_px=1080,
        capture_height_px=1920,
    )

    result = ball_speed_stage(
        clip=Path("does-not-exist.mp4"),
        context=make_context(settings=settings, calibration=calibration),
        seq=None,  # type: ignore[arg-type]
        quality=make_quality(),
        handedness=HandednessResult(
            handedness=Handedness.RIGHT, confidence=0.9, source=HandednessSource.DETECTED
        ),
        contact=make_contact(),
        video=make_video(),
        timer=StageTimer(),
        accumulator=Accumulator(),
    )

    assert result.unavailable_reason is BallSpeedUnavailableReason.DETECTION_DISABLED


def test_a_frame_shape_mismatch_becomes_a_reason_not_an_exception() -> None:
    from app.models.requests import BallSpeedCalibration, NormalizedPoint
    from app.models.enums import CourtReference

    square_preview = BallSpeedCalibration(
        point_a=NormalizedPoint(x=0.2, y=0.3),
        point_b=NormalizedPoint(x=0.8, y=0.75),
        reference=CourtReference.SIDELINE_BASELINE_TO_NET,
        distance_m=11.885,
        capture_width_px=1080,
        capture_height_px=1080,
    )

    result = ball_speed_stage(
        clip=Path("does-not-exist.mp4"),
        context=make_context(calibration=square_preview),
        seq=None,  # type: ignore[arg-type]
        quality=make_quality(),
        handedness=HandednessResult(
            handedness=Handedness.RIGHT, confidence=0.9, source=HandednessSource.DETECTED
        ),
        contact=make_contact(),
        video=make_video(),
        timer=StageTimer(),
        accumulator=Accumulator(),
    )

    assert (
        result.unavailable_reason
        is BallSpeedUnavailableReason.CALIBRATION_FRAME_MISMATCH
    )


def test_unavailable_speed_never_reports_zero() -> None:
    result = unavailable_speed(BallSpeedUnavailableReason.TOO_FEW_DETECTIONS)
    assert result.ball_speed_mph is None
    assert result.detections_used == 0


# --------------------------------------------------------------------------- #
# Stage 4 failure mapping
# --------------------------------------------------------------------------- #


class RaisingStorage:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def download_to_path(self, storage_path: str, destination: Path) -> int:
        raise self.error


def test_download_propagates_the_storage_error_code(tmp_path: Path) -> None:
    from app.services.pipeline import _download

    context = make_context(storage=RaisingStorage(ApiError(ErrorCode.OBJECT_NOT_FOUND)))
    with pytest.raises(JobFailure) as excinfo:
        _download(context, tmp_path / "clip.mp4", StageTimer())

    assert excinfo.value.error_code is ErrorCode.OBJECT_NOT_FOUND
    assert excinfo.value.stage == "download"


def test_an_unexpected_download_failure_is_storage_unavailable(tmp_path: Path) -> None:
    from app.services.pipeline import _download

    context = make_context(storage=RaisingStorage(OSError("connection reset")))
    with pytest.raises(JobFailure) as excinfo:
        _download(context, tmp_path / "clip.mp4", StageTimer())

    assert excinfo.value.error_code is ErrorCode.STORAGE_UNAVAILABLE
    assert excinfo.value.stage == "download"


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #


def test_contact_at_window_edge_fires_only_at_the_edges() -> None:
    assert contact_at_window_edge(make_contact(frame_index=0), 100)
    assert contact_at_window_edge(
        make_contact(frame_index=TRUNCATION_MARGIN_FRAMES - 1), 100
    )
    assert not contact_at_window_edge(
        make_contact(frame_index=TRUNCATION_MARGIN_FRAMES), 100
    )
    assert not contact_at_window_edge(make_contact(frame_index=50), 100)
    assert contact_at_window_edge(make_contact(frame_index=99), 100)
    assert contact_at_window_edge(make_contact(frame_index=0), 0), "no frames is truncated"


def test_accumulator_separates_warnings_from_downgrades() -> None:
    accumulator = Accumulator()
    accumulator.warn("just information")
    assert accumulator.partial is False

    accumulator.degrade("something was not measurable")
    assert accumulator.partial is True
    assert accumulator.warnings == ["just information", "something was not measurable"]


def test_stage_timer_never_reports_a_negative_duration() -> None:
    timer = StageTimer()
    timer.add("pose", 1.0)
    timer.add("pose", -2.0)
    assert timer.ms("pose") == 0
    assert timer.ms("never_measured") == 0
    assert timer.total_ms() >= 0


def test_stage_timings_are_zero_for_stages_that_did_not_run() -> None:
    timer = StageTimer()
    timer.add("download", 0.25)
    timings = stage_timings(timer)

    assert timings.download_ms == 250
    assert timings.ball_detect_ms == 0
    assert timings.ball_speed_ms == 0


def test_feedback_projection_carries_every_field_across_the_model_pair() -> None:
    feedback = FeedbackCoachingFeedback(
        summary="A tidy swing.",
        strengths=["Stable head"],
        improvements=[
            FeedbackImprovement(
                priority=2,
                title="Turn more",
                why="The shoulders open early.",
                cue="Chin on shoulder",
                drill="Shadow swings",
                metric_refs=["shoulder_turn_deg"],
            )
        ],
        source=FeedbackSource.GEMINI,
        model="gemini-2.5-flash",
        guard=FeedbackGuard(
            passed=True,
            rejected_tokens=["99"],
            fields_discarded=["summary"],
            fell_back_to_template=False,
            mph_rule="banned",
        ),
        latency_ms=812,
    )

    wire = to_wire_feedback(feedback)

    assert wire.summary == feedback.summary
    assert wire.strengths == feedback.strengths
    assert wire.source is FeedbackSource.GEMINI
    assert wire.model == "gemini-2.5-flash"
    assert wire.latency_ms == 812
    assert wire.guard.rejected_tokens == ["99"]
    assert wire.guard.mph_rule == "banned"
    assert wire.improvements[0].metric_refs == ["shoulder_turn_deg"]
    assert wire.improvements[0].priority == 2


def test_heartbeat_and_run_async_are_the_only_async_seam() -> None:
    """The runner is synchronous; a coroutine may only travel over run_async."""
    seen: list[str] = []

    async def write() -> str:
        seen.append("ran on a loop")
        return "done"

    context = make_context()
    assert context.run_async(write()) == "done"
    assert seen == ["ran on a loop"]
    assert context.heartbeat() is None
    assert isinstance(datetime.now(), datetime)
