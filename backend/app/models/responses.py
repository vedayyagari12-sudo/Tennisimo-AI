"""Nested response models (PIPELINE.md §2.3).

Only the subset the Stage 7-9 pure layer produces is defined here; the rest of
the HTTP contract belongs to stages that are not implemented yet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.models.enums import (
    AnalysisStatus,
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    CameraView,
    CourtReference,
    ErrorCode,
    FeedbackSource,
    Handedness,
    HandednessSource,
    JobStatus,
    MetricUnit,
    MetricVerdict,
    ScoreCategory,
    ShotType,
    SwingPhaseName,
)


class PoseQuality(BaseModel):
    """Stage 7 output alongside ``NormalizedSequence``."""

    frames_with_pose: int
    frames_missing: int
    detection_rate: float = Field(ge=0.0, le=1.0)
    mean_visibility: float = Field(ge=0.0, le=1.0, description="Mean over the 12 core landmarks.")
    longest_gap_frames: int = Field(
        description=(
            "Longest run of invalid frames anywhere in the analysis window. DIAGNOSTIC "
            "ONLY since PIPELINE.md 7.1: the usability verdict is decided over the core "
            "sub-window below, not by this whole-window worst case."
        )
    )
    interpolated_frames: int = Field(
        description=(
            "Frames whose coordinates were LINEARLY INTERPOLATED across a gap of at most "
            "``MAX_GAP_FRAMES``. Frames inside a longer gap are not interpolated -- they "
            "are edge-held and stay invalid -- and are not counted here."
        )
    )
    torso_scale_px: float = Field(description="Median shoulder-to-hip length in pixels. Defines 1 TU.")
    core_window_start_s: float = Field(
        default=0.0,
        description=(
            "Start of the 2 s core sub-window, in analysis-window time. The core is "
            "anchored on the longest contiguous valid run; the verdict is judged here."
        ),
    )
    core_window_end_s: float = Field(default=0.0, description="End of the core sub-window.")
    core_coverage_fraction: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Share of frames inside the core window that passed the visibility gate.",
    )
    longest_core_gap_frames: int = Field(
        default=0, description="Longest run of invalid frames INSIDE the core window."
    )
    estimated_camera_view: CameraView = CameraView.UNKNOWN
    usable: bool
    flags: list[str] = Field(
        default_factory=list,
        description=(
            "Closed vocabulary, part of the contract -- clients may branch on these. "
            "'ankles_not_visible' | 'wrists_low_visibility' | 'subject_identity_unstable' | "
            "'rotation_retry_applied' | 'motion_scan_coarse' | 'dead_time_outside_core'. "
            "'motion_scan_coarse' means Stage "
            "5 located the analysis window with a whole-clip dense scan at a stride wider than "
            "0.5 s, so the window is coarsely centred. 'subject_identity_unstable' never fails "
            "the job; it "
            "depresses Stage 9 confidence. 'dead_time_outside_core' means the clip was "
            "admitted DESPITE a whole-window gap over the bound, because the core window "
            "is densely tracked -- the audit trail for that tolerance."
        ),
    )


class HandednessResult(BaseModel):
    """Stage 8 output."""

    handedness: Handedness
    confidence: float = Field(ge=0.0, le=1.0)
    source: HandednessSource
    racket_hand_path_length_tu: float | None = Field(default=None)
    off_hand_path_length_tu: float | None = Field(default=None)
    warnings: list[str] = Field(default_factory=list)


class ContactDetection(BaseModel):
    """Stage 9 output."""

    frame_index: int = Field(description="Index into the 30 fps sampled sequence.")
    time_s: float = Field(description="Seconds from analysis_window_start_s.")
    contact_absolute_time_s: float = Field(
        description="analysis_window_start_s + time_s. ABSOLUTE PTS in the source file. This is "
                    "what the ball-detection seek uses; on a long clip with an 8 s window, "
                    "confusing it with time_s measures the wrong 250 ms.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    peak_hand_speed_tu_s: float | None = Field(
        description="Peak racket-hand speed, torso units per second. None = detection failed and "
                    "no speed was measured; 0.0 is a legitimate measured value and must stay "
                    "distinguishable from unavailable.",
    )
    peak_frame_index: int
    prominence_ratio: float = Field(description="Primary peak / next distinct peak. Higher = less ambiguous.")
    method: str = Field(default="peak_speed_decel_onset_v1")
    sanity_flags: list[str] = Field(
        default_factory=list,
        description="Which Stage 9 step-5 gates fired. Each lowers confidence; none rejects.",
    )


class BallSpeedResult(BaseModel):
    """Stage 11 output. The SPEED inside it is nullable; the object is not.

    A deliberately NARROWER subset of the §2.4 model. §2.4 additionally carries
    ``calibration: CalibrationEcho``, ``detection: BallDetectionSummary``,
    ``confidence_caps_applied`` and the constant ``measurement_definition``
    string. Those are omitted here because the stages that own them do not
    exist yet: ``CalibrationEcho`` belongs to ``app/ball/geometry.py`` (11.1,
    unbuilt) and ``BallDetectionSummary`` to the impure frame-acquisition pass
    (Stage 10's runner, unbuilt). This model holds exactly what the pure Stage
    11 estimator can honestly produce on its own, in the same spirit as
    ``SwingMetrics`` documenting what it does and does not cover. Adding the
    deferred sub-objects is additive and does not change these four fields.

    ``ball_speed_mph`` is null whenever any gate fires; it is NEVER a guessed
    or defaulted number, and there is no fallback estimator.
    """

    model_config = ConfigDict(extra="forbid")

    ball_speed_mph: Annotated[StrictInt, Field(ge=15, le=160)] | None = Field(
        default=None,
        description=(
            "Whole miles per hour, or null. StrictInt: a float is a validation error, "
            "not a coercion -- the measurement is not precise enough to justify "
            "decimals. Bounds are §11.4's plausibility bounds, enforced by the type "
            "so an out-of-range value cannot be serialized even if a bug produced it. "
            "Defined as the MEAN speed over the first 0.25 s after contact, not the "
            "instantaneous speed off the racket (§11.2)."
        ),
    )
    confidence: BallSpeedConfidence = Field(
        description=(
            "Derived from detections_used (>=9 high, 5-8 medium, 3-4 low, <3 "
            "unavailable), then capped downward by the §11.4 geometry gates. "
            "Gates can only lower it."
        ),
    )
    detections_used: int = Field(
        ge=0,
        description=(
            "Accepted, associated detections that fed the median, AFTER the first "
            "in-window detection is discarded (§11.2). Sets the confidence tier."
        ),
    )
    unavailable_reason: BallSpeedUnavailableReason | None = Field(
        default=None, description="Non-null iff ball_speed_mph is null."
    )

    @model_validator(mode="after")
    def _reason_pairs_with_null_speed(self) -> BallSpeedResult:
        """The pairing invariant, enforced rather than documented."""
        if (self.ball_speed_mph is None) != (self.unavailable_reason is not None):
            raise ValueError(
                "unavailable_reason must be non-null exactly when ball_speed_mph is null"
            )
        return self


class SwingPhase(BaseModel):
    """One Stage 12 phase (PIPELINE.md §2.3)."""

    name: SwingPhaseName
    start_frame: int
    end_frame: int
    start_time_s: float
    end_time_s: float
    duration_s: float


class SwingPhases(BaseModel):
    """Stage 12 output. Stage 13 only CONSUMES this; segmentation is not implemented."""

    phases: list[SwingPhase] = Field(description="Ordered, contiguous, non-overlapping. Length 5.")
    tempo_ratio: float | None = Field(
        default=None, description="takeback_duration / forward_swing_duration. Dimensionless."
    )


class SwingMetrics(BaseModel):
    """All values in the body-normalized frame. None = not measurable in this clip.

    Ball speed is deliberately NOT a member. It is a physical measurement with
    different error characteristics and lives in AnalysisResponse.ball_speed.

    Every field is ``float | None`` and defaults to ``None``: a metric that
    could not be measured is ``None``, never a default and never ``0.0``
    (PIPELINE.md Stage 13). ``0.0`` is a legitimate measured value for several
    of these fields and must stay distinguishable from "unavailable".
    """

    shoulder_hip_separation_deg: float | None = None
    shoulder_turn_deg: float | None = None
    hip_rotation_deg: float | None = None
    elbow_angle_at_contact_deg: float | None = None
    wrist_lag_deg: float | None = None
    contact_height_ratio: float | None = Field(default=None, description="0 = hip, 1 = shoulder.")
    contact_point_forward_tu: float | None = None
    peak_hand_speed_tu_s: float | None = None
    swing_path_angle_deg: float | None = Field(default=None, description="Positive = low-to-high.")
    swing_plane_deviation_tu: float | None = None
    knee_flexion_min_deg: float | None = None
    weight_transfer_tu: float | None = None
    follow_through_height_tu: float | None = None
    balance_sway_tu: float | None = None
    head_stillness_tu: float | None = None
    wrist_separation_at_contact_tu: float | None = None
    takeback_displacement_tu: float | None = None
    tempo_ratio: float | None = Field(default=None, description="From Stage 12, passed through.")


# ---------------------------------------------------------------------------
# API-layer response models. APPENDED per DATABASE_SETUP.md Part 3.6 and
# Part 5.1; nothing above this line was altered.
#
# The models below cover PIPELINE.md §2.3/§2.4 plus the two list models
# DATABASE_SETUP.md Part 3.4 specifies. The stages that PRODUCE most of them
# (12-18) are still unbuilt; they exist now because the HTTP layer needs the
# shapes to document and to validate stored payloads against.
# ---------------------------------------------------------------------------


class VideoMeta(BaseModel):
    """Stage 5 output, echoed in the analysis (PIPELINE.md §2.3)."""

    duration_s: float = Field(description="Decoded clip duration, seconds.")
    source_fps: float = Field(description="Effective source fps measured from PTS deltas, not metadata.")
    analysis_fps: float = Field(description="Fixed pose resample rate. Always 30.0.")
    rotation_deg: int = Field(description="Container display-matrix rotation applied. 0/90/180/270.")
    width_px: int = Field(description="Pose-stream frame width AFTER rotation and downscale (640 long edge).")
    height_px: int = Field(description="Pose-stream frame height AFTER rotation and downscale.")
    cal_space_width_px: int = Field(description="CAL_SPACE width: post-rotation, 1280 long edge.")
    cal_space_height_px: int = Field(description="CAL_SPACE height.")
    frames_decoded: int
    frames_sampled: int = Field(description="Frames actually fed to MediaPipe. Cap 240.")
    analysis_window_start_s: float
    analysis_window_end_s: float
    motion_scan_used: bool = Field(
        description="True if the keyframe motion scan located the window (clips > 10 s)."
    )


class ShotTypeInference(BaseModel):
    """Stage 14 output. Inferred from TECHNIQUE, never from the ball."""

    shot_type: ShotType
    confidence: float = Field(ge=0.0, le=1.0)
    class_scores: dict[str, float] = Field(description="Per-class scores, sum to 1.0.")
    evidence: list[str] = Field(description="Deterministic Python-generated rationale strings.")
    rule_version: str = Field(default="shot_rules_v1")


class MetricScore(BaseModel):
    """One scored metric (Stage 15).

    NOTE: distinct from ``app.models.feedback.MetricScore``, which is the
    narrower Gemini-payload projection of the same idea. This is the wire model.
    """

    name: str
    value: float | None
    unit: MetricUnit
    verdict: MetricVerdict
    score_0_100: float | None = Field(default=None, description="None iff verdict == unavailable.")
    weight: float = Field(description="Rubric weight within its category, pre-renormalization.")
    ideal_min: float | None = None
    ideal_max: float | None = None
    category: ScoreCategory
    view_sensitive: bool = Field(
        description="True if this value shifts materially with camera yaw. Client should de-emphasize."
    )


class CategoryScore(BaseModel):
    category: ScoreCategory
    score_0_100: float | None
    weight: float
    metric_names: list[str]
    metrics_available: int
    metrics_total: int


class Scorecard(BaseModel):
    overall_score: float | None = Field(default=None, ge=0.0, le=100.0)
    categories: list[CategoryScore]
    metrics: list[MetricScore]
    rubric_version: str = Field(
        default="rubric_v0_placeholder",
        description=(
            "Must equal app.analysis.rubric.RUBRIC_VERSION; pinned by "
            "test_rubric_version_is_one_shared_constant. It cannot IMPORT that "
            "constant -- rubric.py imports this module -- so the literal is "
            "duplicated and the test is what stops the two drifting. "
            "'v0_placeholder' contradicts PIPELINE.md:1013 deliberately: every "
            "band behind it is still marked PLACEHOLDER. See rubric.py."
        ),
    )


class CalibrationEcho(BaseModel):
    """What the server actually derived from the tapped points (PIPELINE.md §2.4)."""

    reference: CourtReference
    distance_m: float
    segment_px: float = Field(description="Tapped-point separation in CAL_SPACE pixels.")
    px_per_m: float = Field(description="segment_px / distance_m. The scale factor.")
    point_a_cal_px: tuple[float, float] = Field(description="Mapped tap A, CAL_SPACE pixels.")
    point_b_cal_px: tuple[float, float] = Field(description="Mapped tap B, CAL_SPACE pixels.")
    frame_shape_check_passed: bool = Field(
        description="Preview aspect matched decoded post-rotation aspect within 2%."
    )


class BallDetectionSummary(BaseModel):
    """Observability for the classical-CV stage. Never contains image data."""

    frames_scanned: int = Field(description="Native-fps frames processed in the measurement window.")
    native_fps: float = Field(description="Measured source fps in the window, from PTS deltas.")
    detection_long_edge_px: int = Field(description="CAL_SPACE long edge. 1280.")
    raw_candidates: int = Field(description="Contours passing either tier, before association.")
    accepted_detections: int = Field(description="Detections associated into the track. Drives confidence.")
    round_tier_detections: int
    streak_tier_detections: int
    coasted_frames: int = Field(description="Frames with no candidate, bridged by prediction.")
    window_start_s: float = Field(description="Absolute PTS.")
    window_end_s: float = Field(description="Absolute PTS. Early-terminated windows end sooner.")
    terminated_by: str = Field(description="'window_end' | 'direction_gate' | 'frame_edge' | 'miss_limit'")
    median_step_px: float | None = None
    median_step_dt_s: float | None = None
    blob_size_drift_ratio: float | None = Field(
        default=None,
        description="minor_axis[last] / minor_axis[first]. Coarse depth-travel proxy; see Stage 10.6.",
    )
    detector_version: str = Field(default="ball_cv_v1")
    elapsed_ms: int


class Improvement(BaseModel):
    priority: int = Field(ge=1, le=3)
    title: str
    why: str = Field(description="Prose explanation. Contains only numbers from the payload.")
    cue: str = Field(description="One short on-court cue.")
    drill: str
    metric_refs: list[str] = Field(description="Names of MetricScore entries this refers to.")


class NumericGuardReport(BaseModel):
    passed: bool
    rejected_tokens: list[str] = Field(default_factory=list)
    fields_discarded: list[str] = Field(default_factory=list)
    fell_back_to_template: bool
    mph_rule: str = Field(
        default="banned",
        description="'banned' when ball_speed_mph is null; 'bound_to:<int>' when it is not. "
        "Recorded so a guard-behaviour regression is visible in stored analyses.",
    )


class CoachingFeedback(BaseModel):
    summary: str
    strengths: list[str]
    improvements: list[Improvement]
    source: FeedbackSource
    model: str | None = Field(default=None, description="e.g. 'gemini-2.5-flash'. None if template.")
    guard: NumericGuardReport
    latency_ms: int | None = None


class StageTimings(BaseModel):
    download_ms: int
    motion_scan_ms: int
    decode_ms: int
    pose_ms: int
    analysis_ms: int
    ball_detect_ms: int = Field(description="0 when the ball stage was skipped.")
    ball_speed_ms: int
    feedback_ms: int
    persist_ms: int
    total_ms: int


class AnalysisError(BaseModel):
    """The error object NESTED inside a 200 poll response whose job failed.

    The key is ``code``, not ``error_code``. That inconsistency with the flat
    HTTP envelope is a known, deliberately deferred wart (DATABASE_SETUP.md
    Part 4.1): ``api_client.dart:306`` reads ``code`` and PIPELINE.md §2.3
    specifies it. Do not "fix" it here unilaterally -- the flat
    ``ErrorEnvelope`` and this model must be changed together.
    """

    code: ErrorCode
    message: str
    stage: str
    retryable: bool


class AnalysisJobStatusResponse(BaseModel):
    """Body A of ``GET /v1/analyses/{id}``: job queued, running, or failed.

    REQUIRED INVARIANT (DATABASE_SETUP.md Part 4.4b): this model has exactly
    five fields and must NEVER grow a ``feedback`` or ``scorecard`` key, not
    even null-valued. The Flutter client discriminates a finished analysis from
    an in-progress job purely on body shape, so adding either key would make
    every in-progress poll parse as a blank finished analysis.

    Serialized with ``exclude_none=False`` so the nulls are present rather than
    stripped.
    """

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    status: JobStatus
    queue_position: int | None = None
    estimated_seconds_remaining: int | None = None
    error: AnalysisError | None = None


class AnalysisResponse(BaseModel):
    """Body B of ``GET /v1/analyses/{id}``: the finished analysis.

    ``status`` is an ``AnalysisStatus`` (``complete``/``partial``), NEVER the
    database's ``succeeded`` (DATABASE_SETUP.md Part 4.4a).

    IMPLEMENTATION NOTE, stated rather than hidden: the poll route returns the
    stored ``analyses.payload`` JSONB **verbatim** as Part 3.3 specifies, and
    does not re-validate it against this model. Two reasons. (1) ``ball_speed``
    below is the NARROW ``BallSpeedResult`` already present in this file, which
    sets ``extra="forbid"`` and lacks §2.4's ``calibration``, ``detection``,
    ``confidence_caps_applied`` and ``measurement_definition``; widening it
    would mean altering a pre-existing model, which this change is not
    permitted to do. (2) Re-validating a stored payload on read means a model
    change can make old rows unreadable, which is the exact failure the JSONB
    column exists to avoid. This model therefore documents the shape for
    OpenAPI and for the writer; it is not a read-path gate.
    """

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    user_id: UUID
    created_at: datetime
    status: AnalysisStatus
    pipeline_version: str = Field(default="v2")

    video: VideoMeta
    pose_quality: PoseQuality
    handedness: HandednessResult
    contact: ContactDetection
    phases: SwingPhases
    shot_type: ShotTypeInference
    metrics: SwingMetrics
    ball_speed: BallSpeedResult = Field(
        description="Always present. ball_speed.ball_speed_mph is the nullable value.",
    )
    scorecard: Scorecard
    feedback: CoachingFeedback
    warnings: list[str] = Field(default_factory=list)
    timings: StageTimings


class AnalysisListItem(BaseModel):
    """One FLAT history row (DATABASE_SETUP.md Part 3.4 / Part 4.5).

    Five top-level keys, no nesting. ``shot_type`` is the bare indexed string,
    not a ``ShotTypeInference``; ``ball_speed_mph`` is a bare int or null, not a
    ``BallSpeedResult``. ``null`` means "not measured" and is NEVER rendered as
    ``0`` -- ``0`` is a measured value and the two must stay distinguishable.
    """

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    created_at: datetime
    shot_type: ShotType
    overall_score: float | None = None
    ball_speed_mph: Annotated[StrictInt, Field(ge=15, le=160)] | None = None


class AnalysisListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AnalysisListItem]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque. Echo it back as ?cursor= to fetch the next page. Null on the "
        "last page. Emitted from day one even though the current client ignores it "
        "(DATABASE_SETUP.md Part 4.2).",
    )


class ErrorEnvelope(BaseModel):
    """THE flat error body. Every non-2xx response has this shape and no other.

    FastAPI's ``{"detail": ...}`` must never escape (DATABASE_SETUP.md Part 3.0).
    The top-level key is ``error_code``; the nested ``AnalysisError`` above uses
    ``code``. See Part 4.1 for why that divergence is deferred, not accidental.
    """

    model_config = ConfigDict(extra="forbid")

    error_code: ErrorCode
    message: str
    retryable: bool
    request_id: str
