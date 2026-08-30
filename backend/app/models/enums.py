"""Enums needed by the feedback module.

Only the subset of PIPELINE.md 2.1 that `app/feedback/` actually uses is defined
here. The remaining enums belong to stages that are not implemented yet.
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
