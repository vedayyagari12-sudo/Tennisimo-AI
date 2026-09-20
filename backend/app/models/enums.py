"""Enums for the feedback module and the HTTP API layer.

The first block is the subset of PIPELINE.md 2.1 that `app/feedback/` uses. The
second block (from `JobStatus` down) was appended for the API layer per
DATABASE_SETUP.md Part 5.1. Members are never reordered or renamed: their wire
values are persisted in `analyses.payload` JSONB.

Enums belonging to stages that are still unimplemented (`BallDetectionTier`,
`SIDE_ON_ALLOWED_REFERENCES`) are deliberately still absent.
"""

from enum import StrEnum


class MetricVerdict(StrEnum):
    LOW = "low"
    IDEAL = "ideal"
    HIGH = "high"
    UNAVAILABLE = "unavailable"


class MetricUnit(StrEnum):
    """Units a SCORED metric may carry.

    Deliberately has NO `mph` member. Ball speed is a measurement, not a scored
    metric.
    """

    DEGREES = "deg"
    TORSO_UNITS = "TU"
    TORSO_UNITS_PER_SEC = "TU/s"
    SECONDS = "s"
    RATIO = "ratio"


class SwingPhaseName(StrEnum):
    READY = "ready"
    TAKEBACK = "takeback"
    FORWARD_SWING = "forward_swing"
    CONTACT = "contact"
    FOLLOW_THROUGH = "follow_through"


class FeedbackSource(StrEnum):
    GEMINI = "gemini"
    GEMINI_PARTIAL = "gemini_partial"
    TEMPLATE = "template"


class BallSpeedConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class BallSpeedUnavailableReason(StrEnum):
    NOT_CALIBRATED = "not_calibrated"
    CONTACT_UNRELIABLE = "contact_unreliable"
    CAMERA_VIEW_UNSUITABLE = "camera_view_unsuitable"
    CALIBRATION_FRAME_MISMATCH = "calibration_frame_mismatch"
    CALIBRATION_IMPLAUSIBLE = "calibration_implausible"
    NO_TRACK_SEEDED = "no_track_seeded"
    TOO_FEW_DETECTIONS = "too_few_detections"
    DEPTH_DRIFT_EXCEEDED = "depth_drift_exceeded"
    DISPLACEMENT_BELOW_NOISE_FLOOR = "displacement_below_noise_floor"
    IMPLAUSIBLE_SPEED = "implausible_speed"
    DETECTION_TIMEOUT = "detection_timeout"
    DETECTION_DISABLED = "detection_disabled"


class Handedness(StrEnum):
    RIGHT = "right"
    LEFT = "left"
    UNKNOWN = "unknown"


class HandednessSource(StrEnum):
    DETECTED = "detected"
    USER_HINT = "user_hint"
    HINT_OVERRODE_DETECTION = "hint_overrode_detection"


class CameraView(StrEnum):
    SIDE_ON = "side_on"
    BEHIND = "behind"
    FRONT = "front"
    OBLIQUE = "oblique"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# API-layer enums (PIPELINE.md §2.1 + DATABASE_SETUP.md Part 3.0.1).
#
# APPENDED, never reordered: several members above are already persisted inside
# stored payloads and in `analyses.payload` JSONB, so their wire values are
# frozen. Everything below is new.
# ---------------------------------------------------------------------------


class JobStatus(StrEnum):
    """Lifecycle of an ``analysis_jobs`` row (DATABASE_SETUP.md Part 3.5).

    REQUIRED INVARIANT (Part 4.4a): this value set and ``AnalysisStatus`` must
    stay disjoint, because the Flutter client discriminates a finished analysis
    from an in-progress job by body shape and status string alone. Asserted by
    ``tests/unit/models/test_status_enums_disjoint.py``.

    ``SUCCEEDED`` is a DATABASE value. It is NEVER emitted by
    ``GET /v1/analyses/{id}``; a finished job returns the full
    ``AnalysisResponse`` whose status is ``complete`` or ``partial``.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisStatus(StrEnum):
    """Status of a produced analysis. Disjoint from ``JobStatus``."""

    COMPLETE = "complete"  # all gates passed
    PARTIAL = "partial"  # produced, but low confidence or missing metrics


class ShotType(StrEnum):
    FOREHAND_TOPSPIN = "forehand_topspin"
    FOREHAND_SLICE = "forehand_slice"
    BACKHAND_ONE_HANDED = "backhand_one_handed"
    BACKHAND_TWO_HANDED = "backhand_two_handed"
    SERVE = "serve"
    VOLLEY = "volley"
    UNKNOWN = "unknown"


class ScoreCategory(StrEnum):
    PREPARATION = "preparation"
    CONTACT = "contact"
    SWING_PATH = "swing_path"
    BALANCE = "balance"
    FOLLOW_THROUGH = "follow_through"


class CourtReference(StrEnum):
    """Court features the user may tap. Canonical lengths in COURT_REFERENCE_METRES."""

    SIDELINE_BASELINE_TO_NET = "sideline_baseline_to_net"  # 11.885 m
    SIDELINE_BASELINE_TO_SERVICE_LINE = "sideline_baseline_to_service_line"  # 5.485 m
    BASELINE_SINGLES_WIDTH = "baseline_singles_width"  # 8.230 m
    BASELINE_DOUBLES_WIDTH = "baseline_doubles_width"  # 10.970 m
    CUSTOM = "custom"


COURT_REFERENCE_METRES: dict[CourtReference, float] = {
    CourtReference.SIDELINE_BASELINE_TO_NET: 11.885,
    CourtReference.SIDELINE_BASELINE_TO_SERVICE_LINE: 5.485,
    CourtReference.BASELINE_SINGLES_WIDTH: 8.230,
    CourtReference.BASELINE_DOUBLES_WIDTH: 10.970,
}
# CourtReference.CUSTOM is deliberately absent: a custom segment has no
# canonical length, which is exactly what makes it custom. Stage 3 skips the
# distance cross-check for it rather than looking up a fabricated value.


class ErrorCode(StrEnum):
    """The complete 20-member set: PIPELINE.md §2.1's 18 plus the two required
    by DATABASE_SETUP.md Part 3.0.1.

    HTTP status and retryability are NOT attributes of the member; they are
    per-code constants held in ``app/api/errors.py`` so this module stays free
    of HTTP concerns.
    """

    AUTH_INVALID_TOKEN = "auth_invalid_token"
    STORAGE_PATH_FORBIDDEN = "storage_path_forbidden"
    OBJECT_NOT_FOUND = "object_not_found"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    UNSUPPORTED_CODEC = "unsupported_codec"
    DECODE_FAILED = "decode_failed"
    NO_VIDEO_STREAM = "no_video_stream"
    VIDEO_TOO_SHORT = "video_too_short"
    VIDEO_TOO_LONG = "video_too_long"
    NO_POSE_DETECTED = "no_pose_detected"
    POSE_QUALITY_TOO_LOW = "pose_quality_too_low"
    CONTACT_NOT_FOUND = "contact_not_found"
    CALIBRATION_INVALID = "calibration_invalid"  # v2: request-time validation only
    QUEUE_FULL = "queue_full"
    WORKER_LOST = "worker_lost"
    INTERNAL_ERROR = "internal_error"
    # --- Added by DATABASE_SETUP.md Part 3.0.1 ---
    INVALID_REQUEST = "invalid_request"
    ANALYSIS_NOT_FOUND = "analysis_not_found"
