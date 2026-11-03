"""Truth matching and the section 11.2 measures (design 11.1). Test-only.

Engine output types do not exist yet (they arrive in build steps 10-16), so
these helpers work on plain numbers and arrays: times, positions, margins,
verdict strings, lengths. Later steps adapt engine results into these calls.

In/out truth: a ball touching a line is good, so a bounce is IN when its
contact point lies within ``CONTACT_PATCH_RADIUS_M`` outside the region's
edge. 0.02 m is the same [estimate] the design uses for the engine; it is a
physical assumption about the ball, not a tuned engine constant.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import numpy as np
import numpy.typing as npt

from app.analysis.court_model import CourtRegion

Float64Array = npt.NDArray[np.float64]

CONTACT_PATCH_RADIUS_M: Final[float] = 0.02
#: Bounce match tolerance in frames (11.2: +/-2 frames).
BOUNCE_MATCH_FRAMES: Final[int] = 2
#: Per-frame candidate match radius (1.4: within 3 px).
FRAME_MATCH_PX: Final[float] = 3.0

#: Section 11.2 targets. Keys are measure names; values are the T0 and T2
#: (1080p60) thresholds. A tuple is an inclusive (low, high) band; a pair of
#: rates (near, far) or (exact, within_one) is noted in the key.
THRESHOLDS_T0: Final[Mapping[str, float | tuple[float, float]]] = MappingProxyType(
    {
        "homography_round_trip_max_m": 1e-6,
        "bounce_recall_min": 0.98,
        "bounce_precision_min": 0.98,
        "median_bounce_error_near_max_m": 0.05,
        "median_along_error_far_max_m": 0.20,
        "in_out_accuracy_outside_band_min": 0.995,
        "confident_wrong_per_1000_max": 5.0,
        "band_coverage_range": (0.92, 0.98),
        "rally_length_exact_min": 0.95,
        "rally_length_within_one_min": 0.99,
        "rally_range_contains_truth_min": 0.99,
        "point_suggestion_wrong_max": 0.01,
        "score_all_known_exact": 1.0,
    }
)
THRESHOLDS_T2_1080P60: Final[Mapping[str, float | tuple[float, float]]] = MappingProxyType(
    {
        "frame_recall_near_min": 0.95,
        "frame_recall_far_min": 0.80,
        "bounce_recall_min": 0.90,
        "bounce_precision_min": 0.90,
        "median_bounce_error_near_max_m": 0.10,
        "median_along_error_far_max_m": 0.35,
        "in_out_accuracy_outside_band_min": 0.99,
        "confident_wrong_per_1000_max": 10.0,
        "band_coverage_range": (0.90, 0.98),
        "rally_length_exact_min": 0.85,
        "rally_length_within_one_min": 0.97,
        "rally_range_contains_truth_min": 0.97,
        "point_suggestion_wrong_max": 0.03,
    }
)


def signed_margin_m(court_xy: tuple[float, float], region: CourtRegion) -> float:
    """Distance inside the region's nearest edge (negative outside), metres.
    Every edge counts, including the net edge of a half."""
    x, y = court_xy
    return min(x - region.x_min, region.x_max - x, y - region.y_min, region.y_max - y)


def is_in(margin_m: float) -> bool:
    """True when the ball's contact patch touches the region."""
    return margin_m + CONTACT_PATCH_RADIUS_M >= 0.0


@dataclass(frozen=True)
class TimeMatch:
    """Greedy one-to-one pairing by nearest time within a tolerance."""

    pairs: tuple[tuple[int, int], ...]  # (truth index, detected index)
    missed: tuple[int, ...]  # truth indices with no detection
    false_positives: tuple[int, ...]  # detected indices with no truth

    @property
    def recall(self) -> float:
        total = len(self.pairs) + len(self.missed)
        return 1.0 if total == 0 else len(self.pairs) / total

    @property
    def precision(self) -> float:
        total = len(self.pairs) + len(self.false_positives)
        return 1.0 if total == 0 else len(self.pairs) / total


def match_times(
    truth_s: Sequence[float], detected_s: Sequence[float], tolerance_s: float
) -> TimeMatch:
    candidates = sorted(
        (abs(t - d), i, j)
        for i, t in enumerate(truth_s)
        for j, d in enumerate(detected_s)
        if abs(t - d) <= tolerance_s
    )
    used_truth: set[int] = set()
    used_detected: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, i, j in candidates:
        if i in used_truth or j in used_detected:
            continue
        used_truth.add(i)
        used_detected.add(j)
        pairs.append((i, j))
    return TimeMatch(
        pairs=tuple(sorted(pairs)),
        missed=tuple(i for i in range(len(truth_s)) if i not in used_truth),
        false_positives=tuple(j for j in range(len(detected_s)) if j not in used_detected),
    )


def per_frame_recall(
    truth_uv: Float64Array,
    detections: Sequence[Float64Array],
    *,
    tolerance_px: float = FRAME_MATCH_PX,
) -> float:
    """Share of frames with a visible ball (finite truth) that have any
    detection within ``tolerance_px``. ``detections[i]`` is (K, 2)."""
    truth = np.asarray(truth_uv, dtype=np.float64)
    visible = np.all(np.isfinite(truth), axis=1)
    if not np.any(visible):
        return 1.0
    hits = 0
    for index in np.flatnonzero(visible):
        found = np.asarray(detections[index], dtype=np.float64).reshape(-1, 2)
        if len(found) and np.min(np.linalg.norm(found - truth[index], axis=1)) <= tolerance_px:
            hits += 1
    return hits / int(np.sum(visible))


def median_error_m(truth_xy: Float64Array, estimated_xy: Float64Array) -> float:
    errors = np.linalg.norm(np.asarray(truth_xy) - np.asarray(estimated_xy), axis=1)
    return float(np.median(errors))


def band_coverage(
    estimated_margins_m: Sequence[float],
    true_margins_m: Sequence[float],
    radii_m: Sequence[float],
) -> float:
    """Share of bounces whose true margin lies within +/- r of the estimate."""
    estimated = np.asarray(estimated_margins_m, dtype=np.float64)
    true = np.asarray(true_margins_m, dtype=np.float64)
    radii = np.asarray(radii_m, dtype=np.float64)
    if len(estimated) == 0:
        return 1.0
    return float(np.mean(np.abs(estimated - true) <= radii))


def confident_wrong_per_1000(verdicts: Sequence[str], truth_in: Sequence[bool]) -> float:
    """Confident (``in``/``out``) calls that contradict truth, per 1000 bounces."""
    if not verdicts:
        return 0.0
    wrong = sum(
        1
        for verdict, actual in zip(verdicts, truth_in, strict=True)
        if verdict in ("in", "out") and (verdict == "in") != actual
    )
    return 1000.0 * wrong / len(verdicts)


def in_out_accuracy_outside_band(
    verdicts: Sequence[str],
    truth_in: Sequence[bool],
    true_margins_m: Sequence[float],
    radii_m: Sequence[float],
) -> float:
    """Accuracy over bounces whose true margin is beyond the band, where a
    ``too_close_to_call`` counts as not correct."""
    total = 0
    correct = 0
    for verdict, actual, margin, radius in zip(
        verdicts, truth_in, true_margins_m, radii_m, strict=True
    ):
        if abs(margin + CONTACT_PATCH_RADIUS_M) <= radius:
            continue
        total += 1
        correct += int(verdict in ("in", "out") and (verdict == "in") == actual)
    return 1.0 if total == 0 else correct / total


@dataclass(frozen=True)
class RallyLengthScores:
    exact: float
    within_one: float
    range_contains_truth: float


def rally_length_scores(
    truth: Sequence[int],
    estimated: Sequence[int],
    minimums: Sequence[int],
    maximums: Sequence[int],
) -> RallyLengthScores:
    if not truth:
        return RallyLengthScores(1.0, 1.0, 1.0)
    pairs = list(zip(truth, estimated, minimums, maximums, strict=True))
    return RallyLengthScores(
        exact=sum(t == e for t, e, _, _ in pairs) / len(pairs),
        within_one=sum(abs(t - e) <= 1 for t, e, _, _ in pairs) / len(pairs),
        range_contains_truth=sum(lo <= t <= hi for t, _, lo, hi in pairs) / len(pairs),
    )
