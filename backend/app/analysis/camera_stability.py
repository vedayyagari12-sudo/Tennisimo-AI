"""Fixed-camera check: decide stable / shaky / moving / unknown (design 2.4).

PURE: numbers in, a verdict out. The measurement (optical flow) is the
pure-CV ``app/ball/camera_motion.py``; this module only judges its samples.

Brief rules (Constants, camera stability):

* ``shifts_px`` is (N, 2), relative to the first sample, in TRACK_SPACE pixels.
* Thresholds are fractions of the long edge, so they are resolution-free:
  p95 <= 0.1 % stable, <= 0.3 % shaky, otherwise moving.
* p95 and rms are computed over PRE-BUMP samples only.
* ``bumped_at_s`` is the first t where the shift magnitude is >= 0.2 % of the
  long edge for every sample in [t, t + 1 s].
* A median tracked-feature count under 30 is ``unknown`` (treated as moving).

Decision taken here (not in the brief): a bump must SETTLE. Read literally,
the bump rule also fires on a continuous pan (it crosses 0.2 % within a second
or two and stays above it), which would leave only a near-zero pre-bump p95 and
call a panning camera "stable, bumped at 0.5 s". A tripod knock ends at a new
fixed position; a pan does not. So an excursion counts as a bump only if it
also contains a 1 s window whose samples lie within ``BUMP_SETTLE_FRACTION`` of
their own median shift. ``bumped_at_s`` is then the start of that excursion,
which satisfies the brief's definition. An unsettled excursion stays in the
p95, so a pan is ``moving``.

Fewer than ``MIN_SAMPLES`` finite samples is ``unknown``: one sample is its own
reference, so its shift is zero by construction and proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from app.models.court import StabilityVerdict

__all__ = [
    "CameraStability",
    "StabilityVerdict",
    "assess_camera_stability",
]

Float64Array = npt.NDArray[np.float64]

STABLE_P95_FRACTION: Final[float] = 0.001
SHAKY_P95_FRACTION: Final[float] = 0.003
BUMP_FRACTION: Final[float] = 0.002
BUMP_MIN_DURATION_S: Final[float] = 1.0
#: Settled means every sample of a 1 s window within this of the window median.
BUMP_SETTLE_FRACTION: Final[float] = 0.001
MIN_MEDIAN_FEATURES: Final[float] = 30.0
MIN_SAMPLES: Final[int] = 2
P95_PERCENT: Final[float] = 95.0
#: Real sample times come from frame PTS, so a nominal 1.0 s span can be a
#: frame short; this much shortfall is accepted.
_TIME_TOLERANCE_S: Final[float] = 0.05


@dataclass(frozen=True)
class CameraStability:
    verdict: StabilityVerdict
    #: None when the verdict is ``unknown``: nothing trustworthy was measured.
    p95_shift_fraction: float | None
    rms_shift_px: float | None
    bumped_at_s: float | None
    samples: int


def _settled_window(
    times: Float64Array, shifts: Float64Array, start: int, stop: int, tolerance_px: float
) -> bool:
    """True when some window of >= 1 s inside samples [start, stop) is steady."""
    for first in range(start, stop):
        last = first
        while last + 1 < stop and times[last + 1] - times[first] <= BUMP_MIN_DURATION_S + _TIME_TOLERANCE_S:
            last += 1
        if times[last] - times[first] < BUMP_MIN_DURATION_S - _TIME_TOLERANCE_S:
            if last == stop - 1:
                return False  # the run ends here; later windows are shorter
            continue  # a sampling gap; a later window may still span 1 s
        window = shifts[first : last + 1]
        centre = np.median(window, axis=0)
        if float(np.max(np.linalg.norm(window - centre, axis=1))) <= tolerance_px:
            return True
    return False


def _bump_index(times: Float64Array, shifts: Float64Array, long_edge_px: int) -> int | None:
    """Index of the first sample of the first settled excursion, or None."""
    magnitude = np.linalg.norm(shifts, axis=1)
    above = magnitude >= BUMP_FRACTION * long_edge_px
    tolerance_px = BUMP_SETTLE_FRACTION * long_edge_px
    index = 0
    count = len(times)
    while index < count:
        if not above[index]:
            index += 1
            continue
        stop = index
        while stop < count and above[stop]:
            stop += 1
        lasts = times[stop - 1] - times[index] >= BUMP_MIN_DURATION_S - _TIME_TOLERANCE_S
        if lasts and _settled_window(times, shifts, index, stop, tolerance_px):
            return index
        index = stop
    return None


def _unknown(samples: int) -> CameraStability:
    return CameraStability(StabilityVerdict.UNKNOWN, None, None, None, samples)


def assess_camera_stability(
    times_s: Float64Array,
    shifts_px: Float64Array,
    feature_counts: npt.NDArray[np.int32],
    long_edge_px: int,
) -> CameraStability:
    """Judge ``N`` samples: times (N,), shifts (N, 2) relative to the first
    sample, tracked-feature counts (N,), all at ``long_edge_px`` (TRACK_SPACE).

    Samples whose shift is not finite (tracking failed) are left out of every
    statistic except the feature-count median, where they count as measured.

    Raises:
        ValueError: on mismatched shapes or a non-positive long edge.
    """
    times = np.asarray(times_s, dtype=np.float64)
    shifts = np.asarray(shifts_px, dtype=np.float64)
    counts = np.asarray(feature_counts)
    if long_edge_px <= 0:
        raise ValueError("long_edge_px must be positive")
    if times.ndim != 1 or shifts.shape != (len(times), 2) or counts.shape != times.shape:
        raise ValueError("expected times (N,), shifts (N, 2) and feature_counts (N,)")
    samples = len(times)
    if samples == 0 or float(np.median(counts)) < MIN_MEDIAN_FEATURES:
        return _unknown(samples)
    finite = np.all(np.isfinite(shifts), axis=1) & np.isfinite(times)
    times, shifts = times[finite], shifts[finite]
    if len(times) < MIN_SAMPLES:
        return _unknown(samples)
    order = np.argsort(times, kind="stable")
    times, shifts = times[order], shifts[order]

    bump = _bump_index(times, shifts, long_edge_px)
    bumped_at_s = None if bump is None else float(times[bump])
    pre = shifts if bump is None else shifts[:bump]
    magnitude = np.linalg.norm(pre, axis=1)
    p95_fraction = float(np.percentile(magnitude, P95_PERCENT)) / float(long_edge_px)
    rms_px = float(np.sqrt(np.mean(magnitude**2)))
    if p95_fraction <= STABLE_P95_FRACTION:
        verdict = StabilityVerdict.STABLE
    elif p95_fraction <= SHAKY_P95_FRACTION:
        verdict = StabilityVerdict.SHAKY
    else:
        verdict = StabilityVerdict.MOVING
    return CameraStability(verdict, p95_fraction, rms_px, bumped_at_s, samples)
