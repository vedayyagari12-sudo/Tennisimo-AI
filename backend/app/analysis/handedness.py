"""Stage 8: handedness detection (PURE, PIPELINE.md 4.4 row 8).

Racket hand = the wrist scoring higher on integrated path length AND on peak
radial distance from mid-hip. Cannot raise: low separation yields
``confidence < 0.5``, and when a hint is present the hint wins.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.normalize import (
    LEFT_WRIST,
    RIGHT_WRIST,
    VISIBILITY_THRESHOLD,
    landmark_visible,
    path_length,
    speed_series,
)
from app.models.enums import Handedness, HandednessSource
from app.models.internal import NormalizedSequence
from app.models.responses import HandednessResult

HINT_CONFIDENCE_FLOOR: Final[float] = 0.5
MARGIN_GAIN: Final[float] = 2.0


class UnknownHandednessError(ValueError):
    """Raised when a racket-hand landmark is requested for an undetermined hand.

    ``Handedness.UNKNOWN`` is not a hand. Returning a wrist index for it would
    make "we could not tell" indistinguishable from "we measured the right arm",
    on a MEASUREMENT INPUT -- every racket-hand metric downstream would be
    computed on an arm nobody established was the racket arm, and would look
    exactly like a normal result. Callers that can degrade (Stage 9 and Stage 13
    both wrap their whole computation and never raise) turn this into an honest
    "unavailable"; callers that cannot must decide explicitly.
    """


def peak_radial_distance(
    points: np.ndarray,
    visibility: np.ndarray | None = None,
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> float:
    """Largest distance from the origin (mid-hip) over a (T, 2) trajectory.

    With ``visibility`` (this landmark's (T,) column) only frames where the
    landmark was individually visible count: a maximum is the statistic most
    easily manufactured by a single occluded frame. All-occluded returns 0.0.
    """
    data = np.asarray(points, dtype=np.float64)
    if data.shape[0] == 0:
        return 0.0
    if visibility is not None:
        visible = landmark_visible(visibility, threshold=threshold)
        data = data[visible]
        if data.shape[0] == 0:
            return 0.0
    return float(np.max(np.linalg.norm(data, axis=1)))


def visible_fraction(
    visibility: np.ndarray, *, threshold: float = VISIBILITY_THRESHOLD
) -> float:
    """Share of frames in which one landmark was individually visible, in [0, 1]."""
    data = np.asarray(visibility, dtype=np.float64)
    if data.shape[0] == 0:
        return 0.0
    return float(np.mean(landmark_visible(data, threshold=threshold)))


def normalized_margin(right_score: float, left_score: float) -> float:
    """Separation between the two wrists, mapped into [0, 1].

    ``|R - L| / (R + L)`` is the share difference; multiplying by
    ``MARGIN_GAIN`` makes a 70/30 split read as confidence 0.8 rather than 0.4,
    and the result is clipped. Degenerate all-zero scores give 0.0.
    """
    total = float(right_score) + float(left_score)
    if total <= 0.0:
        return 0.0
    share = abs(float(right_score) - float(left_score)) / total
    return float(np.clip(share * MARGIN_GAIN, 0.0, 1.0))


def detect_handedness(
    seq: NormalizedSequence, hint: Handedness | None = None
) -> HandednessResult:
    """Stage 8. Never raises."""
    points = np.asarray(seq.points, dtype=np.float64)
    warnings: list[str] = []

    if points.shape[0] < 2:
        return HandednessResult(
            handedness=hint if hint is not None else Handedness.UNKNOWN,
            confidence=0.0,
            source=HandednessSource.USER_HINT if hint is not None else HandednessSource.DETECTED,
            racket_hand_path_length_tu=None,
            off_hand_path_length_tu=None,
            warnings=["sequence too short for handedness detection"],
        )

    right = points[:, RIGHT_WRIST, :]
    left = points[:, LEFT_WRIST, :]

    visibility = np.asarray(seq.visibility, dtype=np.float64)
    has_visibility = visibility.ndim == 2 and visibility.shape[0] == points.shape[0]
    right_visibility = visibility[:, RIGHT_WRIST] if has_visibility else None
    left_visibility = visibility[:, LEFT_WRIST] if has_visibility else None

    right_path = path_length(right, right_visibility)
    left_path = path_length(left, left_visibility)
    right_radius = peak_radial_distance(right, right_visibility)
    left_radius = peak_radial_distance(left, left_visibility)

    # Both wrists are measured only over the frames in which each was actually
    # visible, so each score is an UNDER-estimate in proportion to how much of
    # that wrist was unobserved. The comparison is therefore only as trustworthy
    # as the worse-observed of the two wrists; scaling by that share is what
    # stops a wrist that MediaPipe hallucinated for 80 % of the window from
    # winning -- or losing -- the comparison with a confident-looking margin.
    coverage = 1.0
    if has_visibility:
        right_coverage = visible_fraction(right_visibility)
        left_coverage = visible_fraction(left_visibility)
        coverage = min(right_coverage, left_coverage)
        if coverage < 1.0:
            warnings.append(
                "wrist visibility incomplete "
                f"(right {right_coverage:.2f}, left {left_coverage:.2f} of frames); "
                f"confidence scaled by {coverage:.2f}"
            )

    path_margin = normalized_margin(right_path, left_path)
    radius_margin = normalized_margin(right_radius, left_radius)
    confidence = float(
        np.clip(0.5 * (path_margin + radius_margin) * coverage, 0.0, 1.0)
    )

    right_wins_path = right_path > left_path
    right_wins_radius = right_radius > left_radius

    if right_wins_path == right_wins_radius:
        detected = Handedness.RIGHT if right_wins_path else Handedness.LEFT
    else:
        # The two criteria disagree: tie-break on distance from mid-hip at the
        # peak-speed frame, and say so.
        warnings.append("path_length and peak_radius disagree; resolved by peak-speed frame")
        right_speed = speed_series(right, seq.timestamps_s)
        left_speed = speed_series(left, seq.timestamps_s)
        peak_frame = int(np.argmax(np.maximum(right_speed, left_speed)))
        right_reach = float(np.linalg.norm(right[peak_frame]))
        left_reach = float(np.linalg.norm(left[peak_frame]))
        detected = Handedness.RIGHT if right_reach >= left_reach else Handedness.LEFT
        confidence = min(confidence, HINT_CONFIDENCE_FLOOR - 0.01)

    handedness = detected
    source = HandednessSource.DETECTED
    if confidence < HINT_CONFIDENCE_FLOOR and hint is not None and hint != Handedness.UNKNOWN:
        # Two-handed backhands are the known failure case: both wrists move
        # nearly identically, so the profile hint is the fallback.
        warnings.append(
            f"low separation (confidence {confidence:.2f} < {HINT_CONFIDENCE_FLOOR}); "
            f"user hint {hint.value} applied over detection {detected.value}"
        )
        handedness = hint
        source = HandednessSource.USER_HINT

    racket_is_right = handedness == Handedness.RIGHT
    return HandednessResult(
        handedness=handedness,
        confidence=confidence,
        source=source,
        racket_hand_path_length_tu=right_path if racket_is_right else left_path,
        off_hand_path_length_tu=left_path if racket_is_right else right_path,
        warnings=warnings,
    )


def racket_wrist_index(handedness: Handedness) -> int:
    """Landmark index of the racket wrist. UNKNOWN raises; it never defaults.

    This function used to return the RIGHT wrist for anything that was not
    ``LEFT``, which silently swallowed ``Handedness.UNKNOWN``. See
    :class:`UnknownHandednessError` for why that is a defect rather than a
    convenience. Callers that want to branch instead of catching should test
    ``handedness == Handedness.UNKNOWN`` before calling.
    """
    if handedness == Handedness.LEFT:
        return LEFT_WRIST
    if handedness == Handedness.RIGHT:
        return RIGHT_WRIST
    raise UnknownHandednessError(
        f"no racket wrist for handedness {handedness.value!r}: "
        "the racket hand was never established"
    )
