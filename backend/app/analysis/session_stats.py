"""Session-level statistics (SESSION_MODE.md section 10). PURE, no I/O.

Rules (10.1):
* Only COUNTED swings: ``status == accepted`` and not excluded by the user.
* ``None`` is dropped, never read as 0. A measured 0.0 is kept.
* Nothing that is a mean or a spread crosses shot types. Counts may.
* ``unknown`` swings count in totals and the mix, never in per-shot stats.

Spread is robust (MAD-based), not plain CV: several metrics are signed or have
an arbitrary zero, and their CV explodes near zero for no sporting reason.
Labels compare spread with the rubric band width, and the bands are
PLACEHOLDERS, so every label is provisional and says so. There is no
composite consistency score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np

from app.models.enums import ShotType
from app.models.session import SwingStatus

MAD_SCALE: Final[float] = 1.4826

#: The metrics whose spread is reported, and whether each is on a RATIO scale
#: with a meaningful zero (only then is a robust CV produced).
SPREAD_METRICS: Final[dict[str, bool]] = {
    "contact_height_ratio": False,       # zero is the hip
    "swing_path_angle_deg": False,       # signed around 0
    "follow_through_height_tu": False,   # can cross 0
    "tempo_ratio": True,
    "shoulder_hip_separation_deg": False,
    "peak_hand_speed_tu_s": True,
}
#: Acceptable within one fixed-camera session; never trended BETWEEN sessions.
VIEW_SENSITIVE_SPREAD_METRICS: Final[frozenset[str]] = frozenset({"peak_hand_speed_tu_s"})

MIN_RANGE_N: Final[int] = 5
MIN_LABEL_N: Final[int] = 8
FULL_LABEL_N: Final[int] = 20
TIGHT_MAX_RATIO: Final[float] = 0.5
MODERATE_MAX_RATIO: Final[float] = 1.0

TREND_MIN_N: Final[int] = 15
TREND_MIN_SPAN_S: Final[float] = 300.0
TREND_DELTA_SD_FRACTION: Final[float] = 0.5
TREND_SLOPE_PER_S: Final[float] = 600.0   # slopes are reported per 10 minutes

BETWEEN_MIN_N: Final[int] = 8
BETWEEN_MIN_SESSIONS: Final[int] = 2
#: Same as ``kSteadyBand`` in frontend/lib/models/dashboard_insights.dart.
SCORE_STEADY_BAND: Final[float] = 2.0


class ConsistencyLabel(StrEnum):
    TIGHT = "tight"
    MODERATE = "moderate"
    WIDE = "wide"


class SpreadTreatment(StrEnum):
    """What the sample size lets the UI say (10.2 "Sample sizes")."""

    COUNT_ONLY = "count_only"        # n < 5
    RANGE_ONLY = "range_only"        # 5-7: min to max, no label
    LABEL_WITH_N = "label_with_n"    # 8-19
    LABEL = "label"                  # >= 20


class TrendDirection(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    STEADY = "steady"


@dataclass(frozen=True)
class SwingRecord:
    swing_index: int
    contact_time_s: float
    shot_type: ShotType
    overall_score: float | None
    metrics: Mapping[str, float | None]
    ball_speed_mph: int | None = None
    status: SwingStatus = SwingStatus.ACCEPTED
    user_excluded: bool = False


@dataclass(frozen=True)
class RobustSpread:
    n: int
    median: float
    mad: float
    robust_sd: float


@dataclass(frozen=True)
class ShotMixEntry:
    shot_type: ShotType
    count: int
    percent: float


@dataclass(frozen=True)
class ShotMix:
    total: int
    entries: tuple[ShotMixEntry, ...]


@dataclass(frozen=True)
class MetricConsistency:
    metric: str
    n: int
    treatment: SpreadTreatment
    median: float | None
    robust_sd: float | None
    robust_cv: float | None
    minimum: float | None
    maximum: float | None
    label: ConsistencyLabel | None
    band: tuple[float, float] | None
    view_sensitive: bool
    provisional: bool        # True whenever a label rests on a placeholder band


@dataclass(frozen=True)
class ShotConsistency:
    shot_type: ShotType
    n: int
    overall_score_median: float | None
    metrics: tuple[MetricConsistency, ...]


@dataclass(frozen=True)
class WithinSessionTrend:
    shot_type: ShotType
    metric: str
    n: int
    first_third_median: float
    last_third_median: float
    delta: float
    slope_per_10_min: float
    direction: TrendDirection


@dataclass(frozen=True)
class SessionShotPoint:
    """One past session's summary for one shot type (input to 10.2 between)."""

    session_id: str
    started_at_s: float
    pipeline_version: str
    segmentation_version: str
    shot_type: ShotType
    n: int
    overall_score_median: float | None
    metric_medians: Mapping[str, float | None]


@dataclass(frozen=True)
class BetweenSessionTrend:
    shot_type: ShotType
    sessions: int
    first_score: float
    last_score: float
    delta: float
    direction: TrendDirection
    metric_deltas: Mapping[str, float]


@dataclass(frozen=True)
class BallSpeedSummary:
    shot_type: ShotType
    n_measured: int
    n_missing: int
    median_mph: float | None
    max_mph: int | None


@dataclass(frozen=True)
class SessionStats:
    rubric_version: str
    counted_swings: int
    duration_s: float
    swings_per_minute: float | None
    shot_mix: ShotMix
    consistency: tuple[ShotConsistency, ...]
    trends: tuple[WithinSessionTrend, ...]
    ball_speed: tuple[BallSpeedSummary, ...] | None


# --------------------------------------------------------------------------


def counted(swings: Sequence[SwingRecord]) -> list[SwingRecord]:
    """Accepted, not user-excluded, in a canonical order (input-order free)."""
    kept = [
        s for s in swings if s.status is SwingStatus.ACCEPTED and not s.user_excluded
    ]
    return sorted(kept, key=lambda s: (s.contact_time_s, s.swing_index))


def _present(values: Sequence[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None and np.isfinite(v)]


def robust_spread(values: Sequence[float | None]) -> RobustSpread | None:
    """Median, MAD and robust SD (1.4826 x MAD). ``None`` with fewer than two."""
    data = _present(values)
    if len(data) < 2:
        return None
    array = np.asarray(data, dtype=np.float64)
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return RobustSpread(n=len(data), median=median, mad=mad, robust_sd=MAD_SCALE * mad)


def robust_cv(spread: RobustSpread, *, ratio_scale: bool) -> float | None:
    """Robust SD / |median|, only on a ratio scale and a non-zero median."""
    if not ratio_scale or spread.median == 0.0:
        return None
    return spread.robust_sd / abs(spread.median)


def shot_mix(swings: Sequence[SwingRecord]) -> ShotMix:
    """Counts per shot type over counted swings. ``unknown`` is its own slice."""
    kept = counted(swings)
    total = len(kept)
    entries: list[ShotMixEntry] = []
    for shot_type in ShotType:
        count = sum(1 for s in kept if s.shot_type is shot_type)
        if count:
            entries.append(
                ShotMixEntry(shot_type=shot_type, count=count, percent=100.0 * count / total)
            )
    return ShotMix(total=total, entries=tuple(entries))


def spread_label(
    robust_sd: float, band: tuple[float, float] | None
) -> ConsistencyLabel | None:
    """Spread against the band width. ``None`` without a usable band."""
    if band is None:
        return None
    width = float(band[1]) - float(band[0])
    if width <= 0.0:
        return None
    ratio = robust_sd / width
    if ratio <= TIGHT_MAX_RATIO:
        return ConsistencyLabel.TIGHT
    if ratio <= MODERATE_MAX_RATIO:
        return ConsistencyLabel.MODERATE
    return ConsistencyLabel.WIDE


def treatment_for(n: int) -> SpreadTreatment:
    if n < MIN_RANGE_N:
        return SpreadTreatment.COUNT_ONLY
    if n < MIN_LABEL_N:
        return SpreadTreatment.RANGE_ONLY
    if n < FULL_LABEL_N:
        return SpreadTreatment.LABEL_WITH_N
    return SpreadTreatment.LABEL


def metric_consistency(
    values: Sequence[float | None],
    metric: str,
    band: tuple[float, float] | None,
) -> MetricConsistency:
    data = _present(values)
    n = len(data)
    treatment = treatment_for(n)
    spread = robust_spread(data)
    show_numbers = treatment is not SpreadTreatment.COUNT_ONLY and spread is not None
    show_label = treatment in (SpreadTreatment.LABEL_WITH_N, SpreadTreatment.LABEL)
    label = spread_label(spread.robust_sd, band) if show_label and spread else None
    return MetricConsistency(
        metric=metric,
        n=n,
        treatment=treatment,
        median=spread.median if show_numbers and spread else None,
        robust_sd=spread.robust_sd if show_label and spread else None,
        robust_cv=(
            robust_cv(spread, ratio_scale=SPREAD_METRICS.get(metric, False))
            if show_label and spread
            else None
        ),
        minimum=min(data) if show_numbers else None,
        maximum=max(data) if show_numbers else None,
        label=label,
        band=band,
        view_sensitive=metric in VIEW_SENSITIVE_SPREAD_METRICS,
        provisional=label is not None,
    )


def shot_consistency(
    swings: Sequence[SwingRecord],
    shot_type: ShotType,
    bands: Mapping[str, tuple[float, float]],
) -> ShotConsistency:
    """Per-shot spread of every ``SPREAD_METRICS`` metric. One shot type only."""
    if shot_type is ShotType.UNKNOWN:
        raise ValueError("unknown swings never get per-shot statistics")
    mine = [s for s in counted(swings) if s.shot_type is shot_type]
    scores = _present([s.overall_score for s in mine])
    return ShotConsistency(
        shot_type=shot_type,
        n=len(mine),
        overall_score_median=float(np.median(scores)) if scores else None,
        metrics=tuple(
            metric_consistency(
                [s.metrics.get(metric) for s in mine], metric, bands.get(metric)
            )
            for metric in SPREAD_METRICS
        ),
    )


def theil_sen_slope(times: Sequence[float], values: Sequence[float]) -> float:
    """Median of pairwise slopes. Pairs with equal times are skipped."""
    t = np.asarray(times, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    i, j = np.triu_indices(t.size, k=1)
    dt = t[j] - t[i]
    keep = dt != 0.0
    if not keep.any():
        return 0.0
    return float(np.median((v[j] - v[i])[keep] / dt[keep]))


def within_session_trend(
    swings: Sequence[SwingRecord],
    shot_type: ShotType,
    metric: str,
    *,
    min_n: int = TREND_MIN_N,
    min_span_s: float = TREND_MIN_SPAN_S,
) -> WithinSessionTrend | None:
    """First-third vs last-third median, confirmed by a Theil-Sen slope.

    A measured change only. Why it changed (fatigue, shot selection, court
    position) is not measured, and nothing downstream may say it is.
    """
    if shot_type is ShotType.UNKNOWN:
        return None
    rows = [
        (s.contact_time_s, float(s.metrics[metric]))
        for s in counted(swings)
        if s.shot_type is shot_type
        and s.metrics.get(metric) is not None
        and np.isfinite(float(s.metrics[metric]))  # type: ignore[arg-type]
    ]
    if len(rows) < min_n:
        return None
    times = [t for t, _ in rows]
    values = [v for _, v in rows]
    if times[-1] - times[0] < min_span_s:
        return None
    third = len(rows) // 3
    first = float(np.median(values[:third]))
    last = float(np.median(values[-third:]))
    delta = last - first
    slope = theil_sen_slope(times, values)
    spread = robust_spread(values)
    sd = spread.robust_sd if spread is not None else 0.0
    direction = TrendDirection.STEADY
    if abs(delta) > TREND_DELTA_SD_FRACTION * sd and np.sign(delta) == np.sign(slope) != 0:
        direction = TrendDirection.RISING if delta > 0 else TrendDirection.FALLING
    return WithinSessionTrend(
        shot_type=shot_type,
        metric=metric,
        n=len(rows),
        first_third_median=first,
        last_third_median=last,
        delta=delta,
        slope_per_10_min=slope * TREND_SLOPE_PER_S,
        direction=direction,
    )


def between_session_trend(
    points: Sequence[SessionShotPoint],
    *,
    min_sessions: int = BETWEEN_MIN_SESSIONS,
    min_n: int = BETWEEN_MIN_N,
) -> BetweenSessionTrend | None:
    """Score and metric medians across sessions of ONE shot type.

    Only sessions on the newest session's pipeline and segmentation versions
    count: a version change can move every number. View-sensitive metrics are
    not trended, because the camera setup differs between sessions.
    """
    if not points:
        return None
    shot_types = {p.shot_type for p in points}
    if len(shot_types) != 1:
        raise ValueError("between-session trends are per shot type")
    ordered = sorted(points, key=lambda p: (p.started_at_s, p.session_id))
    newest = ordered[-1]
    usable = [
        p
        for p in ordered
        if p.pipeline_version == newest.pipeline_version
        and p.segmentation_version == newest.segmentation_version
        and p.n >= min_n
        and p.overall_score_median is not None
    ]
    if len(usable) < min_sessions:
        return None
    first, last = usable[0], usable[-1]
    assert first.overall_score_median is not None and last.overall_score_median is not None
    delta = last.overall_score_median - first.overall_score_median
    if abs(delta) < SCORE_STEADY_BAND:
        direction = TrendDirection.STEADY
    else:
        direction = TrendDirection.RISING if delta > 0 else TrendDirection.FALLING
    metric_deltas: dict[str, float] = {}
    for metric in SPREAD_METRICS:
        if metric in VIEW_SENSITIVE_SPREAD_METRICS:
            continue
        a, b = first.metric_medians.get(metric), last.metric_medians.get(metric)
        if a is not None and b is not None:
            metric_deltas[metric] = float(b) - float(a)
    return BetweenSessionTrend(
        shot_type=newest.shot_type,
        sessions=len(usable),
        first_score=first.overall_score_median,
        last_score=last.overall_score_median,
        delta=delta,
        direction=direction,
        metric_deltas=metric_deltas,
    )


def ball_speed_summary(swings: Sequence[SwingRecord]) -> tuple[BallSpeedSummary, ...]:
    """Per shot type: measured speeds only. Never estimated from hand speed."""
    kept = counted(swings)
    out: list[BallSpeedSummary] = []
    for shot_type in ShotType:
        if shot_type is ShotType.UNKNOWN:
            continue
        mine = [s for s in kept if s.shot_type is shot_type]
        if not mine:
            continue
        speeds = [s.ball_speed_mph for s in mine if s.ball_speed_mph is not None]
        out.append(
            BallSpeedSummary(
                shot_type=shot_type,
                n_measured=len(speeds),
                n_missing=len(mine) - len(speeds),
                median_mph=float(np.median(speeds)) if speeds else None,
                max_mph=max(speeds) if speeds else None,
            )
        )
    return tuple(out)


def session_stats(
    swings: Sequence[SwingRecord],
    duration_s: float,
    bands_by_shot: Mapping[ShotType, Mapping[str, tuple[float, float]]],
    ball_speeds_requested: bool,
    *,
    rubric_version: str,
) -> SessionStats:
    """Everything section 10 shows for one session. Deterministic."""
    kept = counted(swings)
    present_shots = [
        t for t in ShotType if t is not ShotType.UNKNOWN and any(s.shot_type is t for s in kept)
    ]
    consistency = tuple(
        shot_consistency(kept, t, bands_by_shot.get(t, {})) for t in present_shots
    )
    trends = tuple(
        trend
        for t in present_shots
        for metric in SPREAD_METRICS
        if (trend := within_session_trend(kept, t, metric)) is not None
    )
    minutes = float(duration_s) / 60.0
    return SessionStats(
        rubric_version=rubric_version,
        counted_swings=len(kept),
        duration_s=float(duration_s),
        swings_per_minute=len(kept) / minutes if minutes > 0.0 else None,
        shot_mix=shot_mix(kept),
        consistency=consistency,
        trends=trends,
        ball_speed=ball_speed_summary(kept) if ball_speeds_requested else None,
    )
