"""Stage 15b: the scorecard (PURE, PIPELINE.md 4.4 row 15).

A ``SwingMetrics`` and a ``ShotType`` in, a ``Scorecard`` out. No I/O, no arrays,
no network. The bands come from ``analysis/rubric.py``; this module only applies
the ramp, redistributes weight and aggregates.

``None`` NEVER BECOMES 0.0
--------------------------
This is the constraint the whole module turns on, and ``0.0`` is a legitimate
MEASURED value for ``contact_point_forward_tu``, ``swing_path_angle_deg`` and
``head_stillness_tu`` (``metrics.py:23-28``), so the two must stay
distinguishable at every level:

1. Metric -- ``value is None`` gives ``score_0_100 = None`` and
   ``verdict = unavailable``. The metric still appears in ``Scorecard.metrics``
   with its weight and category, so a client can show what was not measurable.
2. Category -- unavailable metrics are excluded and their weight is
   redistributed proportionally across the REMAINING metrics of the SAME
   category. Never across categories.
3. Category with zero available metrics -- ``score_0_100 = None`` and
   ``metrics_available = 0``, branched on BEFORE any division. Guarding the
   division instead and letting it return something plausible is the single
   most likely place for a silent ``0.0`` to be born.
4. Overall -- a weight-normalized mean over the categories that HAVE a score,
   with the category weights renormalized over exactly those.
5. Too few categories, or none -- ``overall_score = None``. The ``Scorecard`` is
   still returned, with populated ``categories`` and ``metrics``, never omitted.

NO BLANKET EXCEPTION WRAPPER, DELIBERATELY
------------------------------------------
Stages 9, 12 and 13 wrap themselves because they consume raw arrays that can be
degenerate in ways the type system does not capture. Stage 15 consumes two
pydantic-validated values, every division here is branched (not merely guarded),
and "unmeasurable" is a supported VALUE rather than an error -- so there is no
failure mode that a wrapper could convert into a meaningful partial result. A
crash here is a bug and should surface as such, not be laundered into an empty
scorecard that reads exactly like an honestly unscoreable clip. That laundering
is the defect this change fixed in ``metrics.py``; reproducing it here, where
``Scorecard`` has no ``warnings`` field to carry the difference, would be worse.
"""

from __future__ import annotations

import math

from app.analysis.rubric import (
    CATEGORY_WEIGHTS,
    METRIC_SPECS,
    MIN_SCORED_CATEGORIES,
    RUBRIC_VERSION,
    BandDirection,
    MetricBand,
    bands_for,
    is_view_sensitive,
)
from app.models.enums import MetricVerdict, ScoreCategory, ShotType
from app.models.responses import CategoryScore, MetricScore, Scorecard, SwingMetrics

SCORE_MAX: float = 100.0
SCORE_DECIMALS: int = 1


def score_from_band(
    value: float | None, band: MetricBand
) -> tuple[float | None, MetricVerdict]:
    """The piecewise-linear ramp (PIPELINE.md:1009). Returns ``(score, verdict)``.

    ``100`` inside the ideal band, decaying linearly to ``0`` at ``hard_min`` /
    ``hard_max`` and flat ``0`` beyond. A ``lower_is_better`` band is flat ``100``
    all the way down: for those metrics zero is PERFECT, and scoring a perfectly
    still head as ``low`` would generate coaching advice to move it more.
    """
    if value is None or not math.isfinite(float(value)):
        return None, MetricVerdict.UNAVAILABLE
    number = float(value)
    lower_is_better = band.direction == BandDirection.LOWER_IS_BETTER

    if number > band.ideal_max:
        span = band.hard_max - band.ideal_max
        if span <= 0.0 or number >= band.hard_max:
            return 0.0, MetricVerdict.HIGH
        score = SCORE_MAX * (band.hard_max - number) / span
        return round(score, SCORE_DECIMALS), MetricVerdict.HIGH

    if number >= band.ideal_min or lower_is_better:
        return SCORE_MAX, MetricVerdict.IDEAL

    span = band.ideal_min - band.hard_min
    if span <= 0.0 or number <= band.hard_min:
        return 0.0, MetricVerdict.LOW
    score = SCORE_MAX * (number - band.hard_min) / span
    return round(score, SCORE_DECIMALS), MetricVerdict.LOW


def score_metric(name: str, value: float | None, band: MetricBand) -> MetricScore:
    """One wire ``MetricScore``. ``score_0_100`` is None iff verdict is unavailable."""
    score, verdict = score_from_band(value, band)
    return MetricScore(
        name=name,
        value=None if value is None or not math.isfinite(float(value)) else float(value),
        unit=band.unit,
        verdict=verdict,
        score_0_100=score,
        weight=band.weight,
        ideal_min=band.ideal_min,
        ideal_max=band.ideal_max,
        category=band.category,
        view_sensitive=is_view_sensitive(name),
    )


def score_category(
    category: ScoreCategory, scored: list[MetricScore]
) -> CategoryScore:
    """Aggregate one category, redistributing the weight of unavailable metrics.

    Redistribution is WITHIN the category only. A category with no available
    metric scores ``None`` -- explicitly, before any division -- and never 0.0.
    """
    members = [metric for metric in scored if metric.category == category]
    available = [metric for metric in members if metric.score_0_100 is not None]
    weight = float(CATEGORY_WEIGHTS[category])

    if not available:
        return CategoryScore(
            category=category,
            score_0_100=None,
            weight=weight,
            metric_names=[metric.name for metric in members],
            metrics_available=0,
            metrics_total=len(members),
        )

    total_weight = sum(float(metric.weight) for metric in available)
    if total_weight <= 0.0:
        # Unreachable with the shipped table (every weight is > 0 by model
        # constraint), and still not allowed to become a 0.0 score.
        return CategoryScore(
            category=category,
            score_0_100=None,
            weight=weight,
            metric_names=[metric.name for metric in members],
            metrics_available=0,
            metrics_total=len(members),
        )

    score = sum(
        float(metric.weight) / total_weight * float(metric.score_0_100 or 0.0)
        for metric in available
    )
    return CategoryScore(
        category=category,
        score_0_100=round(score, SCORE_DECIMALS),
        weight=weight,
        metric_names=[metric.name for metric in members],
        metrics_available=len(available),
        metrics_total=len(members),
    )


def overall_from_categories(categories: list[CategoryScore]) -> float | None:
    """Weight-normalized mean over the categories that HAVE a score.

    ``None`` -- never 0.0 -- when fewer than ``MIN_SCORED_CATEGORIES`` of the
    five scored. An ``overall_score`` computed over a varying subset of
    categories is not comparable across clips, and it is persisted as the user's
    progress history, so a thin one would silently compare unlike things.
    """
    scored = [item for item in categories if item.score_0_100 is not None]
    if len(scored) < MIN_SCORED_CATEGORIES:
        return None
    total_weight = sum(float(item.weight) for item in scored)
    if total_weight <= 0.0:
        return None
    overall = sum(
        float(item.weight) / total_weight * float(item.score_0_100 or 0.0)
        for item in scored
    )
    return round(overall, SCORE_DECIMALS)


def build_scorecard(metrics: SwingMetrics, shot_type: ShotType) -> Scorecard:
    """Stage 15. Always returns a ``Scorecard``; the SCORES inside it are nullable.

    All five categories are always present, including ones this shot type has no
    bands for -- a client showing "contact: not scored" is telling the truth,
    and omitting the row would make the gap invisible.
    """
    bands = bands_for(shot_type)
    scored = [
        score_metric(name, getattr(metrics, name, None), bands[name])
        for name in METRIC_SPECS
        if name in bands
    ]
    categories = [score_category(category, scored) for category in CATEGORY_WEIGHTS]
    return Scorecard(
        overall_score=overall_from_categories(categories),
        categories=categories,
        metrics=scored,
        rubric_version=RUBRIC_VERSION,
    )
