"""The real Stage 4-18 job runner.

IMPURE by construction: it downloads, decodes, runs MediaPipe, calls Gemini and
writes the database row. Every DECISION it makes is delegated to a pure function
in ``app/analysis`` or to one of the two documented I/O seams
(``app/pose/video_io.py``, ``app/ball/frames.py``); this module owns the ORDER,
the failure mapping and the clock, and nothing else.

Order is ``docs/PIPELINE_STAGES_12_14_15.md`` Part E.1, and the part of it that
looks arbitrary is not: **Stages 12-15 run BEFORE Stages 10-11.** Shot type is
inferred from technique, never from the ball (CLAUDE.md), and a function that
runs before the ball track exists cannot consult it even by accident.

THREADING
---------
This runner is SYNCHRONOUS and runs on the orchestrator's executor thread. It
has no event loop of its own, so every coroutine -- the Stage 4 download, the
Stage 18 write, each heartbeat -- is marshalled onto the application loop
through ``JobContext.run_async`` / ``JobContext.heartbeat``.

BALL SPEED NEVER FAILS THE JOB
------------------------------
Absent calibration skips Stages 10 and 11 entirely -- no second decode pass at
all -- and yields ``ball_speed_mph = null`` with
``unavailable_reason = "not_calibrated"``. That is the common case: it is not a
fault, and per Part E.4 it is not grounds for ``partial``.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import numpy as np

from app.analysis.contact import CONTACT_NOT_FOUND, detect_contact_frame
from app.analysis.handedness import detect_handedness, racket_wrist_index
from app.analysis.metrics import compute_swing_metrics
from app.analysis.normalize import normalize_sequence
from app.analysis.phases import segment_swing_phases
from app.analysis.rubric import RUBRIC_VERSION, is_unscored_metric_warning
from app.analysis.scoring import build_scorecard
from app.analysis.shot_type import infer_shot_type, resolve_shot_type
from app.analysis.speed import compute_ball_speed
from app.api.errors import ApiError
from app.ball.frames import run_ball_detection
from app.ball.geometry import CalibrationRejected, map_calibration_points
from app.feedback.gemini import GeminiClient, GoogleGenAIClient, generate_feedback
from app.feedback.projection import build_feedback_input
from app.models.enums import (
    AnalysisStatus,
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    CourtReference,
    ErrorCode,
    Handedness,
    ShotType,
)
from app.models.feedback import CoachingFeedback as FeedbackCoachingFeedback
from app.models.internal import NormalizedSequence
from app.models.responses import (
    AnalysisResponse,
    BallSpeedResult,
    CoachingFeedback,
    ContactDetection,
    HandednessResult,
    Improvement,
    NumericGuardReport,
    PoseQuality,
    ShotTypeInference,
    StageTimings,
    SwingPhases,
    VideoMeta,
)
from app.pose.extractor import (
    PoseExtractor,
    extract_keypoints,
    resolve_model_path,
    verify_model_asset,
)
from app.pose.sequence import build_pose_sequence, detection_rate
from app.pose.video_io import MOTION_SCAN_COARSE_FLAG, VideoDecodeError, open_pose_stream
from app.services.orchestrator import JobContext, JobFailure
from app.services.repository import AnalysisRow

logger = logging.getLogger("tennisform.pipeline")

#: PIPELINE.md Stage 6: fewer than 40 % of sampled frames carrying a pose is
#: ``no_pose_detected``. ``extract_keypoints`` deliberately does NOT apply this
#: gate itself -- it reports ``detected`` and the orchestrator decides.
MIN_DETECTION_RATE: Final[float] = 0.40

#: Part E.4 / PIPELINE.md:553. Below this the technique analysis is ``partial``
#: -- but it is still produced. Low contact confidence never fails the job.
MIN_CONTACT_CONFIDENCE: Final[float] = 0.35

#: Stage 7 flag that downgrades to ``partial`` without failing (Part E.4).
SUBJECT_IDENTITY_UNSTABLE: Final[str] = "subject_identity_unstable"

#: Stage 9's degenerate return (``contact.py:515``). One of the two contact
#: conditions that fail the job; low confidence alone does not (Part E.2).
SEQUENCE_UNUSABLE: Final[str] = "sequence_unusable"

#: Stage 9's OTHER failing condition (PIPELINE.md 9.2.3): the sequence was
#: perfectly analysable and every enumerated candidate was physically
#: implausible. Distinct from ``sequence_unusable`` in cause, identical in
#: consequence -- both map to ``ErrorCode.CONTACT_NOT_FOUND``, because Stage 9
#: cannot raise and refusing is the whole point of the filter. Part E.2.
CONTACT_FAILING_FLAGS: Final[tuple[str, ...]] = (SEQUENCE_UNUSABLE, CONTACT_NOT_FOUND)

#: How close to either end of the sampled sequence the contact frame may sit
#: before the swing is reported as running off the end of the analysis window.
#: INVENTED: no document specifies it. Two frames is the narrowest band that
#: catches a contact at the very edge without firing on an ordinary swing. What
#: it feeds is ``FeedbackInput.truncated_clip``, which suppresses Gemini.
TRUNCATION_MARGIN_FRAMES: Final[int] = 2

#: v3: ``swing_path_angle_deg`` is folded onto the swing's own direction of
#: travel, so a leftward-travelling swing no longer reads ~180 deg off. v2 rows
#: with a leftward swing carry the old value and must not be trended against v3.
#:
#: v4: ``shoulder_turn_deg`` and ``hip_rotation_deg`` are the NET AXIAL turn
#: (0-90 deg) over reliable frames, replacing v3's max - min of an unwrapped
#: series (which read up to 491.8 deg on real footage). A frame whose body line
#: is foreshortened (shoulder < 0.10 TU, hip < 0.08 TU) is unreliable; fewer
#: than 5 reliable frames gives None plus a ``*_line_foreshortened`` /
#: ``*_line_too_few_frames`` warning. v3 rows must not be trended against v4.
#: Also v4: Stage 8 reports ``racket_hand_path_length_tu`` /
#: ``off_hand_path_length_tu`` as None (not 0.0) for a wrist never observed
#: across one frame-to-frame step, so an unseen wrist no longer reads as the
#: shortest path and a false "volley 1.00" (v3 serve 4902143 -> unknown).
#: Also v4 (no second bump): the request's ``label_hint``, when present and not
#: ``unknown``, is the PRIMARY shot type and selects the rubric, so a declared
#: forehand is scored on the forehand bands (declared serve/volley/backhand
#: still score on the generic ones). The classifier runs as a cross-check.
#: Also v4 (no second bump): ``shoulder_turn_deg``, ``hip_rotation_deg`` and
#: ``wrist_lag_deg`` are no longer SCORED (rubric ``rubric_v0.1_placeholder``):
#: the first two measure in-plane tilt, not turn, and wrist lag is blind to the
#: hand. They are still computed into ``SwingMetrics`` as diagnostics, but are in
#: no category, no ``overall_score``, no ``Scorecard.metrics`` row and no Gemini
#: payload, and their ``*_line_*`` warnings are logged, not shown, and never make
#: a job ``partial``. Preparation is now ``shoulder_hip_separation_deg`` alone.
#: Also v4: the request's concrete ``handedness_hint`` (right/left) ALWAYS sets
#: the racket hand (``source = user_hint``), even over a confident detection;
#: detection runs as a cross-check (``HandednessResult.detected_handedness``)
#: and a confident disagreement adds one plain note through ``warn`` (never
#: ``partial``). v3 let a detection at >= 0.5 override the user (serve 4902773
#: tracked the ball-toss arm). A wrong user choice is now unrecoverable.
PIPELINE_VERSION: Final[str] = "v4"

#: The one plain-language warning added when the user's declared shot type and
#: a confident technique reading disagree. A NOTE, not a downgrade: it never
#: makes an analysis ``partial`` (see ``record_shot_type``).
SHOT_TYPE_MISMATCH_NOTE: Final[str] = (
    "You picked this shot type, but the swing looked a bit like a different "
    "shot to our analysis. We used your choice. If it was a different shot, "
    "pick that one next time."
)


class StageTimer:
    """Accumulates elapsed milliseconds per stage name. Monotonic wall clock."""

    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._started: float = time.monotonic()

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self.add(name, time.monotonic() - started)

    def add(self, name: str, seconds: float) -> None:
        self._totals[name] = self._totals.get(name, 0.0) + float(seconds)

    def ms(self, name: str) -> int:
        return max(0, int(round(self._totals.get(name, 0.0) * 1000.0)))

    def total_ms(self) -> int:
        return max(0, int(round((time.monotonic() - self._started) * 1000.0)))


@dataclass
class DecodeMeter:
    """Time spent inside the Stage 5 generator, separated from inference time.

    Stage 5 is lazy: a frame is decoded by ``next()`` from INSIDE the Stage 6
    loop, so a naive timer bills every millisecond of decoding to ``pose_ms``.
    Wrapping the generator is the only way to report the two honestly.
    """

    seconds: float = 0.0

    def wrap(
        self, frames: Iterator[tuple[float, np.ndarray]]
    ) -> Iterator[tuple[float, np.ndarray]]:
        while True:
            started = time.monotonic()
            try:
                item = next(frames)
            except StopIteration:
                self.seconds += time.monotonic() - started
                return
            self.seconds += time.monotonic() - started
            yield item


@dataclass
class Accumulator:
    """Warnings and partial-status reasons collected across the pure stages."""

    warnings: list[str] = field(default_factory=list)
    low_confidence: list[str] = field(default_factory=list)
    partial: bool = False

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def degrade(self, message: str) -> None:
        """Record a warning that ALSO downgrades the analysis to ``partial``."""
        self.warnings.append(message)
        self.partial = True


def record_shot_type(accumulator: Accumulator, shot_type: ShotTypeInference) -> None:
    """Stage 14's contribution to warnings and status, from the RESOLVED shot type.

    * An ``unknown`` PRIMARY type still downgrades to ``partial`` (unchanged).
      A declared type is never ``unknown``, so a declaration that the
      classifier could not confirm does not downgrade the analysis.
    * A mismatch adds :data:`SHOT_TYPE_MISMATCH_NOTE` through ``warn``, never
      ``degrade``: the user's declaration was honoured, nothing is less
      trustworthy, so the status must not change because of it.
    """
    if shot_type.shot_type is ShotType.UNKNOWN:
        accumulator.degrade("shot type could not be determined from technique")
    if shot_type.mismatch:
        accumulator.warn(SHOT_TYPE_MISMATCH_NOTE)


def record_handedness(accumulator: Accumulator, handedness: HandednessResult) -> None:
    """Stage 8's contribution to warnings. Never to the status.

    Every Stage 8 warning goes through ``warn``, never ``degrade`` -- including
    ``handedness.HANDEDNESS_MISMATCH_NOTE``: the user picked a hand, that choice
    was used, and a confident detection disagreeing with it makes nothing less
    trustworthy than the user's own answer, so the status must not change.
    """
    for warning in handedness.warnings:
        accumulator.warn(warning)


def record_metric_warnings(accumulator: Accumulator, warnings: list[str]) -> None:
    """Stage 13's contribution to warnings, Gemini's low-confidence list and status.

    Any Stage 13 warning makes the analysis ``partial`` and is shown -- EXCEPT
    one that only says ``shoulder_turn_deg`` / ``hip_rotation_deg`` could not be
    measured. Those concern a number that is in no score and shown to nobody
    (``rubric.UNSCORED_DIAGNOSTIC_METRICS``), so they are logged at debug and
    never reach ``warnings``, ``low_confidence`` or the status.
    """
    kept: list[str] = []
    for warning in warnings:
        if is_unscored_metric_warning(warning):
            logger.debug("unscored diagnostic metric warning: %s", warning)
        else:
            kept.append(warning)
    accumulator.warnings.extend(kept)
    accumulator.low_confidence.extend(kept)
    if kept:
        accumulator.partial = True


def unavailable_speed(reason: BallSpeedUnavailableReason) -> BallSpeedResult:
    """A null speed paired with its reason. Never a zero, never a guess."""
    return BallSpeedResult(
        ball_speed_mph=None,
        confidence=BallSpeedConfidence.UNAVAILABLE,
        detections_used=0,
        unavailable_reason=reason,
    )


def to_wire_feedback(feedback: FeedbackCoachingFeedback) -> CoachingFeedback:
    """``models.feedback.CoachingFeedback`` -> ``models.responses.CoachingFeedback``.

    Two DIFFERENT models with the same name, exactly like the ``MetricScore``
    pair that ``feedback/projection.py`` exists for. They agree field for field
    today, so this is a rename-free copy -- written out by hand anyway so that
    adding a field to either one becomes a type error here instead of a silent
    drop on the wire.
    """
    return CoachingFeedback(
        summary=feedback.summary,
        strengths=list(feedback.strengths),
        improvements=[
            Improvement(
                priority=item.priority,
                title=item.title,
                why=item.why,
                cue=item.cue,
                drill=item.drill,
                metric_refs=list(item.metric_refs),
            )
            for item in feedback.improvements
        ],
        source=feedback.source,
        model=feedback.model,
        guard=NumericGuardReport(
            passed=feedback.guard.passed,
            rejected_tokens=list(feedback.guard.rejected_tokens),
            fields_discarded=list(feedback.guard.fields_discarded),
            fell_back_to_template=feedback.guard.fell_back_to_template,
            mph_rule=feedback.guard.mph_rule,
            model_finish_reason=feedback.guard.model_finish_reason,
        ),
        latency_ms=feedback.latency_ms,
    )


def contact_at_window_edge(contact: ContactDetection, frame_count: int) -> bool:
    """True when the detected contact sits at the edge of the analysed window."""
    if frame_count <= 0:
        return True
    index = int(contact.frame_index)
    return (
        index < TRUNCATION_MARGIN_FRAMES
        or index >= frame_count - TRUNCATION_MARGIN_FRAMES
    )


def stage_timings(timer: StageTimer) -> StageTimings:
    """Wall-clock milliseconds per stage.

    ``motion_scan_ms`` is 0 unless Stage 5 reported a scan, in which case it is
    the whole ``open_pose_stream`` call -- probe plus keyframe scan -- because
    the two cannot be separated without timing inside that function.
    """
    return StageTimings(
        download_ms=timer.ms("download"),
        motion_scan_ms=timer.ms("motion_scan"),
        decode_ms=timer.ms("decode"),
        pose_ms=timer.ms("pose"),
        analysis_ms=timer.ms("analysis"),
        ball_detect_ms=timer.ms("ball_detect"),
        ball_speed_ms=timer.ms("ball_speed"),
        feedback_ms=timer.ms("feedback"),
        persist_ms=timer.ms("persist"),
        total_ms=timer.total_ms(),
    )


def _download(context: JobContext, destination: Path, timer: StageTimer) -> None:
    """Stage 4. A Storage failure is a job failure, with Storage's own code."""
    with timer.measure("download"):
        try:
            context.run_async(
                context.storage.download_to_path(context.storage_path, destination)
            )
        except ApiError as exc:
            raise JobFailure(exc.error_code, exc.message, stage="download") from exc
        except Exception as exc:  # noqa: BLE001 - anything else is still Storage
            raise JobFailure(ErrorCode.STORAGE_UNAVAILABLE, stage="download") from exc


def ball_speed_stage(
    *,
    clip: Path,
    context: JobContext,
    seq: NormalizedSequence,
    quality: PoseQuality,
    handedness: HandednessResult,
    contact: ContactDetection,
    video: VideoMeta,
    timer: StageTimer,
    accumulator: Accumulator,
) -> BallSpeedResult:
    """Stages 10 and 11. NEVER raises and never fails the job (Part E.2/E.3)."""
    calibration = context.request.ball_speed_calibration
    if calibration is None:
        return unavailable_speed(BallSpeedUnavailableReason.NOT_CALIBRATED)
    if not context.settings.ball_detection_enabled:
        return unavailable_speed(BallSpeedUnavailableReason.DETECTION_DISABLED)

    try:
        echo = map_calibration_points(
            calibration,
            container_rotation_deg=video.rotation_deg,
            cal_width_px=video.cal_space_width_px,
            cal_height_px=video.cal_space_height_px,
        )
    except CalibrationRejected as rejected:
        return unavailable_speed(rejected.reason)

    if handedness.handedness is Handedness.UNKNOWN:
        # ``racket_wrist_index`` RAISES on UNKNOWN rather than defaulting to the
        # right wrist (``handedness.py:185-200``), and Stage 10's seed is a
        # racket-wrist position. No racket hand means no seed, which is
        # ``no_track_seeded`` -- stated as a reason, never as a job failure.
        accumulator.warn(
            "ball speed skipped: the racket hand was never established, so the "
            "detector could not be seeded"
        )
        return unavailable_speed(BallSpeedUnavailableReason.NO_TRACK_SEEDED)

    with timer.measure("ball_detect"):
        result, _summary, window_times = run_ball_detection(
            clip,
            seq,
            contact_absolute_time_s=contact.contact_absolute_time_s,
            racket_wrist_index=racket_wrist_index(handedness.handedness),
            contact_frame_index=contact.frame_index,
            cal_width_px=video.cal_space_width_px,
            cal_height_px=video.cal_space_height_px,
            pose_long_edge_px=max(video.width_px, video.height_px),
            rotation_deg=video.rotation_deg,
            px_per_m=echo.px_per_m,
            deadline_s=context.settings.ball_detection_deadline_s,
        )

    with timer.measure("ball_speed"):
        return compute_ball_speed(
            result.track,
            timestamps_s=window_times,
            contact_abs_s=contact.contact_absolute_time_s,
            contact_confidence=contact.confidence,
            px_per_m=echo.px_per_m,
            segment_px=echo.segment_px,
            is_custom_reference=calibration.reference is CourtReference.CUSTOM,
            estimated_camera_view=quality.estimated_camera_view,
            detection_reason=result.reason,
        )


def run_analysis_job(
    context: JobContext, *, gemini_client: GeminiClient | None = None
) -> None:
    """Stages 4-18 for one clip, on the executor thread.

    ``gemini_client`` is injected so a test can replay a fixture instead of
    calling the API. ``None`` builds the real client from ``Settings``; the key
    comes from the environment and is never logged.

    Raises:
        JobFailure: with the Part E.2 ``ErrorCode`` for the stage that failed.
    """
    timer = StageTimer()
    accumulator = Accumulator()

    with tempfile.TemporaryDirectory(prefix="tennisform-") as workdir:
        clip = Path(workdir) / "clip.mp4"

        # -- Stage 4: download -------------------------------------------
        _download(context, clip, timer)
        context.heartbeat()

        # -- Stage 5: decode + sample ------------------------------------
        with timer.measure("decode"):
            try:
                stream = open_pose_stream(
                    clip,
                    long_edge_px=context.settings.target_long_edge_px,
                    cal_long_edge_px=context.settings.ball_cal_space_long_edge_px,
                    analysis_window_s=context.settings.analysis_window_seconds,
                    analysis_fps=context.settings.analysis_fps,
                    max_frames=context.settings.max_analysis_frames,
                    motion_scan_threshold_s=context.settings.motion_scan_threshold_s,
                )
            except VideoDecodeError as exc:
                raise JobFailure(exc.error_code, str(exc), stage="decode") from exc
        if stream.motion_scan_used:
            timer.add("motion_scan", timer.ms("decode") / 1000.0)
        context.heartbeat()

        # -- Stage 6: MediaPipe ------------------------------------------
        try:
            model_path = resolve_model_path()
            verify_model_asset(model_path)
        except Exception as exc:  # noqa: BLE001 - a bad bundle is our bug
            raise JobFailure(
                ErrorCode.INTERNAL_ERROR, "pose model asset unusable", stage="pose"
            ) from exc

        meter = DecodeMeter()
        with timer.measure("pose"):
            try:
                with PoseExtractor(model_path) as extractor:
                    raw = extract_keypoints(
                        extractor,
                        meter.wrap(stream.frames),
                        # Stage 5 yields ABSOLUTE PTS and extract_keypoints
                        # subtracts the window start ITSELF
                        # (extractor.py:215-228). Pre-subtracting it here would
                        # subtract it twice, invisibly on any short clip.
                        window_start_s=stream.analysis_window_start_s,
                        width_px=stream.width_px,
                        height_px=stream.height_px,
                        max_frames=context.settings.max_analysis_frames,
                    )
            except VideoDecodeError as exc:
                raise JobFailure(exc.error_code, str(exc), stage="decode") from exc
        # Decoding happened inside the loop above: move it to the decode bill.
        timer.add("decode", meter.seconds)
        timer.add("pose", -meter.seconds)
        context.heartbeat()

        video = stream.video_meta()
        if detection_rate(raw.detected) < MIN_DETECTION_RATE:
            raise JobFailure(ErrorCode.NO_POSE_DETECTED, stage="pose")

        pose_sequence = build_pose_sequence(raw, stream.width_px, stream.height_px)

        # -- Stages 7-9 and 12-15: the pure core -------------------------
        with timer.measure("analysis"):
            seq, quality = normalize_sequence(pose_sequence)
            if stream.motion_scan_coarse and MOTION_SCAN_COARSE_FLAG not in quality.flags:
                # A degraded window-location path must cost something visible in
                # the response, not only in the logs (PIPELINE.md 5.1.1).
                quality.flags.append(MOTION_SCAN_COARSE_FLAG)
            if not quality.usable:
                raise JobFailure(ErrorCode.POSE_QUALITY_TOO_LOW, stage="normalize")
            if SUBJECT_IDENTITY_UNSTABLE in quality.flags:
                accumulator.degrade("subject identity was unstable across the clip")

            handedness = detect_handedness(seq, context.request.handedness_hint)
            record_handedness(accumulator, handedness)

            contact = detect_contact_frame(
                seq,
                handedness,
                quality,
                analysis_window_start_s=stream.analysis_window_start_s,
            )
            if any(flag in contact.sanity_flags for flag in CONTACT_FAILING_FLAGS):
                raise JobFailure(ErrorCode.CONTACT_NOT_FOUND, stage="contact")
            if contact.confidence < MIN_CONTACT_CONFIDENCE:
                message = (
                    f"contact confidence {contact.confidence:.2f} is below "
                    f"{MIN_CONTACT_CONFIDENCE:.2f}"
                )
                accumulator.degrade(message)
                accumulator.low_confidence.append(message)

            phases, phase_warnings = segment_swing_phases(seq, handedness, contact)
            accumulator.warnings.extend(phase_warnings)
            accumulator.low_confidence.extend(phase_warnings)
            if phases is None:
                accumulator.degrade("swing phases could not be segmented")
            elif phase_warnings:
                # A collapsed phase reports itself through a warning (Part A.4).
                accumulator.partial = True

            metrics, metric_warnings = compute_swing_metrics(
                seq, handedness, contact, phases
            )
            record_metric_warnings(accumulator, metric_warnings)

            # The user's declaration, when there is one, is the PRIMARY type;
            # the classifier still runs and is kept as the cross-check.
            shot_type = resolve_shot_type(
                context.request.label_hint,
                infer_shot_type(metrics, seq, handedness, contact, phases),
                contact.confidence,
                min_contact_confidence=MIN_CONTACT_CONFIDENCE,
            )
            record_shot_type(accumulator, shot_type)

            # Rubric selection follows the PRIMARY type.
            scorecard = build_scorecard(metrics, shot_type.shot_type)
            scored_categories = sum(
                1 for c in scorecard.categories if c.score_0_100 is not None
            )
            if scorecard.overall_score is None:
                accumulator.degrade("no scoring category had a measurable metric")
            elif scored_categories < len(scorecard.categories):
                accumulator.degrade(
                    f"{scored_categories} of {len(scorecard.categories)} scoring "
                    "categories were measurable"
                )
        context.heartbeat()

        # -- Stages 10-11: ball speed. Never fails the job ---------------
        ball_speed = ball_speed_stage(
            clip=clip,
            context=context,
            seq=seq,
            quality=quality,
            handedness=handedness,
            contact=contact,
            video=video,
            timer=timer,
            accumulator=accumulator,
        )
        context.heartbeat()

    # The clip file is gone from here on: everything below is in memory.

    # -- Stages 16-17: payload + Gemini ----------------------------------
    truncated = contact_at_window_edge(contact, int(seq.timestamps_s.shape[0]))
    if truncated:
        accumulator.degrade("contact sits at the edge of the analysis window")

    with timer.measure("feedback"):
        core = build_feedback_input(
            scorecard=scorecard,
            shot_type=shot_type,
            handedness=handedness.handedness.value,
            contact_confidence=contact.confidence,
            ball_speed=ball_speed,
            truncated_clip=truncated,
            low_confidence_warnings=accumulator.low_confidence,
            pipeline_version=PIPELINE_VERSION,
        )
        client = (
            gemini_client
            if gemini_client is not None
            else GoogleGenAIClient(
                api_key=context.settings.gemini_api_key.get_secret_value(),
                model=context.settings.gemini_model,
            )
        )
        feedback = to_wire_feedback(generate_feedback(core, client))
    context.heartbeat()

    # -- Assemble ---------------------------------------------------------
    status = AnalysisStatus.PARTIAL if accumulator.partial else AnalysisStatus.COMPLETE
    created_at = datetime.now(UTC)
    response = AnalysisResponse(
        analysis_id=context.job_id,
        user_id=context.user_id,
        created_at=created_at,
        status=status,
        pipeline_version=PIPELINE_VERSION,
        video=video,
        pose_quality=quality,
        handedness=handedness,
        contact=contact,
        # ``AnalysisResponse.phases`` is not Optional, but Stage 12 may
        # legitimately return None. An EMPTY phase list is the honest encoding:
        # no phase was segmented, and no phase was invented either.
        phases=phases if phases is not None else SwingPhases(phases=[], tempo_ratio=None),
        shot_type=shot_type,
        metrics=metrics,
        ball_speed=ball_speed,
        scorecard=scorecard,
        feedback=feedback,
        warnings=accumulator.warnings,
        timings=stage_timings(timer),
    )

    # -- Stage 18: persist -------------------------------------------------
    with timer.measure("persist"):
        row = AnalysisRow(
            id=context.job_id,
            user_id=context.user_id,
            storage_path=context.storage_path,
            status=status,
            shot_type=shot_type.shot_type,
            overall_score=scorecard.overall_score,
            ball_speed_mph=ball_speed.ball_speed_mph,
            ball_speed_unavailable_reason=ball_speed.unavailable_reason,
            pipeline_version=PIPELINE_VERSION,
            rubric_version=RUBRIC_VERSION,
            payload=response.model_dump(mode="json"),
            created_at=created_at,
        )
        try:
            context.run_async(context.repository.persist_success(row))
        except ApiError as exc:
            raise JobFailure(exc.error_code, exc.message, stage="persist") from exc
        except Exception as exc:  # noqa: BLE001
            raise JobFailure(ErrorCode.INTERNAL_ERROR, stage="persist") from exc


__all__ = [
    "MIN_CONTACT_CONFIDENCE",
    "MIN_DETECTION_RATE",
    "PIPELINE_VERSION",
    "CONTACT_FAILING_FLAGS",
    "SEQUENCE_UNUSABLE",
    "SHOT_TYPE_MISMATCH_NOTE",
    "TRUNCATION_MARGIN_FRAMES",
    "Accumulator",
    "ball_speed_stage",
    "DecodeMeter",
    "StageTimer",
    "contact_at_window_edge",
    "record_handedness",
    "record_metric_warnings",
    "record_shot_type",
    "run_analysis_job",
    "stage_timings",
    "to_wire_feedback",
    "unavailable_speed",
]
