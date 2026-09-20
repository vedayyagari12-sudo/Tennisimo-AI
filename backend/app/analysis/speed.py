"""Stage 11: ball-speed estimation (PURE, PIPELINE.md Stage 11).

Numpy and stdlib only. No cv2, no I/O, no network, no clock. Every failure path
returns ``ball_speed_mph=None`` plus a specific
:class:`~app.models.enums.BallSpeedUnavailableReason`; nothing here raises and
nothing here fabricates a number. There is no fallback estimator.

What this module owns, and what it deliberately does not:

* It owns the estimator of PIPELINE.md 11.2-11.4: the measurement window, the
  per-step median of ``d_i / dt_i``, the confidence tier and the downward caps.
* It does NOT own calibration (11.1). ``map_calibration_points()`` -- raw tapped
  preview points to ``px_per_m`` -- belongs to ``app/ball/geometry.py``, which
  does not exist yet. The calibration facts arrive here as already-computed
  scalars: ``px_per_m``, ``segment_px`` and ``is_custom_reference``.
* It does NOT re-run association. Bounce/gap/frame-edge termination is
  ``app/ball/track.py``'s job (10.5) and is already baked into the
  :class:`~app.ball.track.BallTrack` it is handed; ``terminated_by`` is
  respected as a statement of where the window already ended rather than
  recomputed here.

Timestamps. ``BallTrack`` deliberately carries no temporal fields -- its own
docstring says so, because they come from the impure frame-acquisition pass.
Time therefore arrives as an explicit ``timestamps_s`` parameter, one entry per
frame in the stack the track's ``frame_offsets`` index into, exactly as
``NormalizedSequence.timestamps_s`` is plumbed elsewhere. No fps is assumed:
phone video is VFR (Stage 5) and each step is normalized by its own dt.

Coordinate space: CAL_SPACE pixels, y DOWN (PIPELINE.md 10.1). Unchanged here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np
import numpy.typing as npt

from app.ball.track import BallTrack
from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    CameraView,
)
from app.models.responses import BallSpeedResult

# --------------------------------------------------------------------------- #
# Constants -- PIPELINE.md 11.2 - 11.4. No magic numbers below this block.
# --------------------------------------------------------------------------- #

#: 11.2 -- the measurement window, in seconds after contact. Short on purpose:
#: a ball decelerating at ~18 m/s^2 makes a longer window read systematically
#: low, and a drag correction would require spin, which is out of scope.
WINDOW_S: Final[float] = 0.25

#: 11.2 -- at and just after contact the ball blob is merged with the racket
#: head, so the first in-window detection (and with it the first inter-frame
#: step) is discarded before anything is measured.
DISCARDED_LEADING_DETECTIONS: Final[int] = 1

MPH_PER_M_PER_S: Final[float] = 2.23694

# 11.4 confidence tiers, by the number of accepted detections that fed the
# estimate. Set by the median's breakdown behaviour, not by taste.
MIN_DETECTIONS: Final[int] = 3
MEDIUM_DETECTIONS: Final[int] = 5
HIGH_DETECTIONS: Final[int] = 9

# 11.4 downward caps.
CONTACT_CONFIDENCE_FLOOR: Final[float] = 0.35
MIN_STEP_DISPLACEMENT_PX: Final[float] = 4.0
SHORT_SEGMENT_PX: Final[float] = 150.0
MIN_SEGMENT_PX: Final[float] = 60.0
#: 10.6 mitigation 4 -- minor-axis drift bands. Coarse by design: this is a
#: gross-error detector for depth travel, not a depth sensor.
DRIFT_NO_PENALTY_LOW: Final[float] = 0.75
DRIFT_NO_PENALTY_HIGH: Final[float] = 1.33
DRIFT_CAP_LOW: Final[float] = 0.60
DRIFT_CAP_HIGH: Final[float] = 1.67

# 11.4 plausibility bounds, also enforced by the Pydantic field itself.
MIN_PLAUSIBLE_MPH: Final[int] = 15
MAX_PLAUSIBLE_MPH: Final[int] = 160

#: Tier ordering, so a cap can only ever lower the level.
_TIER_RANK: Final[dict[BallSpeedConfidence, int]] = {
    BallSpeedConfidence.UNAVAILABLE: 0,
    BallSpeedConfidence.LOW: 1,
    BallSpeedConfidence.MEDIUM: 2,
    BallSpeedConfidence.HIGH: 3,
}


# --------------------------------------------------------------------------- #
# Pure helpers -- arrays in, numbers out
# --------------------------------------------------------------------------- #


def accepted_window_indices(
    track: BallTrack,
    timestamps_s: npt.NDArray[np.float64],
    contact_abs_s: float,
    *,
    window_s: float = WINDOW_S,
) -> npt.NDArray[np.int64]:
    """Row indices of ``track`` inside ``[contact_abs_s, contact_abs_s + window_s]``.

    The first surviving detection is then dropped (PIPELINE.md 11.2): its blob
    is merged with the racket, so the first reliable displacement runs from the
    second in-window detection onward. Rows whose ``frame_offsets`` fall outside
    ``timestamps_s`` are silently ignored rather than raising.

    The window's EARLY end -- direction gate, frame-edge margin, third coasted
    frame -- is not recomputed here: the track was already truncated by
    ``app/ball/track.py`` when it fired.
    """
    offsets = np.asarray(track.frame_offsets, dtype=np.int64).reshape(-1)
    times = np.asarray(timestamps_s, dtype=np.float64).reshape(-1)
    if offsets.size == 0 or times.size == 0:
        return np.zeros((0,), dtype=np.int64)
    rows = np.arange(offsets.size, dtype=np.int64)
    in_stack = (offsets >= 0) & (offsets < times.size)
    rows, offsets = rows[in_stack], offsets[in_stack]
    if rows.size == 0:
        return np.zeros((0,), dtype=np.int64)
    detection_times = times[offsets]
    in_window = (detection_times >= float(contact_abs_s)) & (
        detection_times <= float(contact_abs_s) + float(window_s)
    )
    return np.asarray(rows[in_window][DISCARDED_LEADING_DETECTIONS:], dtype=np.int64)


def step_displacements_px(xy_px: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """``d_i = ||xy[i+1] - xy[i]||`` for each consecutive pair, in CAL_SPACE px."""
    points = np.asarray(xy_px, dtype=np.float64).reshape(-1, 2)
    if points.shape[0] < 2:
        return np.zeros((0,), dtype=np.float64)
    return np.asarray(np.linalg.norm(np.diff(points, axis=0), axis=1), dtype=np.float64)


def step_speeds_px_per_s(
    displacements_px: npt.NDArray[np.float64], dt_s: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """``d_i / dt_i`` for the steps whose dt is positive.

    Each step is normalized by ITS OWN dt because phone video is VFR and native
    decoding presents genuinely uneven PTS deltas; with constant dt this is
    algebraically identical to dividing a median displacement by a single dt.
    """
    d = np.asarray(displacements_px, dtype=np.float64).reshape(-1)
    dt = np.asarray(dt_s, dtype=np.float64).reshape(-1)
    if d.size == 0 or d.size != dt.size:
        return np.zeros((0,), dtype=np.float64)
    usable = np.isfinite(d) & np.isfinite(dt) & (dt > 0.0)
    return np.asarray(d[usable] / dt[usable], dtype=np.float64)


def blob_size_drift_ratio(minor_axis_px: npt.NDArray[np.float64]) -> float | None:
    """``minor_axis[last] / minor_axis[first]`` over the accepted window (10.6).

    Returns ``None`` when the ratio is not computable -- fewer than two entries,
    or a non-finite or non-positive first value. ``None`` means "no drift
    signal", and the caller then applies NO drift cap at all: a missing proxy is
    not evidence of depth travel, and inventing a 1.0 would silently assert the
    ball stayed at constant range.
    """
    values = np.asarray(minor_axis_px, dtype=np.float64).reshape(-1)
    if values.size < 2:
        return None
    first, last = float(values[0]), float(values[-1])
    if not np.isfinite(first) or not np.isfinite(last) or first <= 0.0:
        return None
    return last / first


def confidence_from_detections(detections_used: int) -> BallSpeedConfidence:
    """11.4 base tier: 0-2 unavailable, 3-4 low, 5-8 medium, >=9 high."""
    n = int(detections_used)
    if n >= HIGH_DETECTIONS:
        return BallSpeedConfidence.HIGH
    if n >= MEDIUM_DETECTIONS:
        return BallSpeedConfidence.MEDIUM
    if n >= MIN_DETECTIONS:
        return BallSpeedConfidence.LOW
    return BallSpeedConfidence.UNAVAILABLE


def cap_confidence(
    level: BallSpeedConfidence, cap: BallSpeedConfidence
) -> BallSpeedConfidence:
    """Lowest wins. A cap can only lower the tier, never raise it."""
    return cap if _TIER_RANK[cap] < _TIER_RANK[level] else level


def mph_from_px_per_s(speed_px_per_s: float, px_per_m: float) -> int:
    """``v_px / px_per_m -> m/s -> mph``, rounded to a whole number.

    ``px_per_m`` is pixels-per-metre, so it is a DIVISION. Multiplying here is
    the single most expensive sign error available in this file.
    """
    metres_per_second = float(speed_px_per_s) / float(px_per_m)
    return int(round(metres_per_second * MPH_PER_M_PER_S))


# --------------------------------------------------------------------------- #
# The estimator
# --------------------------------------------------------------------------- #


def _unavailable(
    reason: BallSpeedUnavailableReason, detections_used: int = 0
) -> BallSpeedResult:
    """Every failure path in this module funnels through here."""
    return BallSpeedResult(
        ball_speed_mph=None,
        confidence=BallSpeedConfidence.UNAVAILABLE,
        detections_used=max(int(detections_used), 0),
        unavailable_reason=reason,
    )


def compute_ball_speed(
    track: BallTrack | None,
    *,
    timestamps_s: npt.NDArray[np.float64] | Sequence[float],
    contact_abs_s: float,
    contact_confidence: float,
    px_per_m: float,
    segment_px: float,
    is_custom_reference: bool,
    estimated_camera_view: CameraView = CameraView.UNKNOWN,
    detection_reason: BallSpeedUnavailableReason | None = None,
) -> BallSpeedResult:
    """PIPELINE.md 11.2-11.4, end to end. NEVER raises.

    ``timestamps_s`` holds one absolute PTS per frame in the stack that
    ``track.frame_offsets`` indexes; ``contact_abs_s`` is
    ``ContactDetection.contact_absolute_time_s`` -- an ABSOLUTE PTS, not
    ``time_s``.

    ``track is None`` means the upstream detector produced no track. The reason
    it produced none is the detector's to state, so ``detection_reason``
    (i.e. ``BallDetectionResult.reason``) is passed straight through when given;
    with nothing given the honest answer is ``NO_TRACK_SEEDED``. ``NOT_CALIBRATED``
    is deliberately NOT the default for that case -- it is reserved for an
    unusable ``px_per_m``, which is the only calibration fact this module can
    actually check.

    ``coasted_frames`` is a scalar summary of the whole track, not a per-step
    flag, so no coasting-specific gate can be reconstructed from it here; the
    third-consecutive-coast termination it summarizes already truncated the
    track upstream.
    """
    try:
        if track is None:
            return _unavailable(
                detection_reason or BallSpeedUnavailableReason.NO_TRACK_SEEDED
            )

        # Gates that do not depend on the track at all, cheapest and most
        # upstream first. Each is explicit; the try/except is belt, not braces.
        if (
            not np.isfinite(contact_confidence)
            or float(contact_confidence) < CONTACT_CONFIDENCE_FLOOR
        ):
            return _unavailable(BallSpeedUnavailableReason.CONTACT_UNRELIABLE)
        if estimated_camera_view in (CameraView.FRONT, CameraView.BEHIND):
            return _unavailable(BallSpeedUnavailableReason.CAMERA_VIEW_UNSUITABLE)
        if not np.isfinite(px_per_m) or float(px_per_m) <= 0.0:
            return _unavailable(BallSpeedUnavailableReason.NOT_CALIBRATED)
        if not np.isfinite(segment_px) or float(segment_px) < MIN_SEGMENT_PX:
            return _unavailable(BallSpeedUnavailableReason.CALIBRATION_IMPLAUSIBLE)

        times = np.asarray(timestamps_s, dtype=np.float64).reshape(-1)
        rows = accepted_window_indices(track, times, float(contact_abs_s))
        detections_used = int(rows.size)
        level = confidence_from_detections(detections_used)
        if level == BallSpeedConfidence.UNAVAILABLE:
            return _unavailable(
                BallSpeedUnavailableReason.TOO_FEW_DETECTIONS, detections_used
            )

        xy = np.asarray(track.xy_px, dtype=np.float64).reshape(-1, 2)
        minor = np.asarray(track.minor_axis_px, dtype=np.float64).reshape(-1)
        if rows.max(initial=-1) >= xy.shape[0]:
            return _unavailable(BallSpeedUnavailableReason.TOO_FEW_DETECTIONS)

        drift = (
            blob_size_drift_ratio(minor[rows])
            if rows.max(initial=-1) < minor.size
            else None
        )
        if drift is not None and not (DRIFT_CAP_LOW <= drift <= DRIFT_CAP_HIGH):
            return _unavailable(
                BallSpeedUnavailableReason.DEPTH_DRIFT_EXCEEDED, detections_used
            )

        offsets = np.asarray(track.frame_offsets, dtype=np.int64).reshape(-1)[rows]
        displacements = step_displacements_px(xy[rows])
        dt = np.diff(times[offsets])
        speeds = step_speeds_px_per_s(displacements, dt)
        if speeds.size == 0:
            return _unavailable(
                BallSpeedUnavailableReason.TOO_FEW_DETECTIONS, detections_used
            )

        # Median, not mean: the failure mode of a greedy associator is one
        # catastrophically wrong step, not Gaussian noise (11.3).
        if float(np.median(displacements)) < MIN_STEP_DISPLACEMENT_PX:
            return _unavailable(
                BallSpeedUnavailableReason.DISPLACEMENT_BELOW_NOISE_FLOOR,
                detections_used,
            )
        mph = mph_from_px_per_s(float(np.median(speeds)), float(px_per_m))
        if not MIN_PLAUSIBLE_MPH <= mph <= MAX_PLAUSIBLE_MPH:
            return _unavailable(
                BallSpeedUnavailableReason.IMPLAUSIBLE_SPEED, detections_used
            )

        # Downward caps: lowest wins, none can raise the tier.
        if estimated_camera_view == CameraView.OBLIQUE:
            level = cap_confidence(level, BallSpeedConfidence.LOW)
        if drift is not None and not (
            DRIFT_NO_PENALTY_LOW <= drift <= DRIFT_NO_PENALTY_HIGH
        ):
            level = cap_confidence(level, BallSpeedConfidence.LOW)
        if bool(is_custom_reference):
            level = cap_confidence(level, BallSpeedConfidence.MEDIUM)
        if float(segment_px) < SHORT_SEGMENT_PX:
            level = cap_confidence(level, BallSpeedConfidence.LOW)

        return BallSpeedResult(
            ball_speed_mph=mph,
            confidence=level,
            detections_used=detections_used,
            unavailable_reason=None,
        )
    except Exception:  # noqa: BLE001 -- Stage 11 contract: cannot raise.
        # A computation we could not complete produced no usable detections.
        return _unavailable(BallSpeedUnavailableReason.TOO_FEW_DETECTIONS)


__all__: Sequence[str] = (
    "CONTACT_CONFIDENCE_FLOOR",
    "DISCARDED_LEADING_DETECTIONS",
    "DRIFT_CAP_HIGH",
    "DRIFT_CAP_LOW",
    "DRIFT_NO_PENALTY_HIGH",
    "DRIFT_NO_PENALTY_LOW",
    "HIGH_DETECTIONS",
    "MAX_PLAUSIBLE_MPH",
    "MEDIUM_DETECTIONS",
    "MIN_DETECTIONS",
    "MIN_PLAUSIBLE_MPH",
    "MIN_SEGMENT_PX",
    "MIN_STEP_DISPLACEMENT_PX",
    "MPH_PER_M_PER_S",
    "SHORT_SEGMENT_PX",
    "WINDOW_S",
    "accepted_window_indices",
    "blob_size_drift_ratio",
    "cap_confidence",
    "compute_ball_speed",
    "confidence_from_detections",
    "mph_from_px_per_s",
    "step_displacements_px",
    "step_speeds_px_per_s",
)
