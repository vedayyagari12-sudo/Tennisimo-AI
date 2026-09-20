"""Stage 15b unit tests: the ramp, the redistribution, and the ``None`` rules.

Every assertion about an unavailable value is written ``is None``, never
``== 0``: ``0 == 0.0`` is true and ``None``-vs-``0`` bugs hide behind
truthiness. ``0.0`` is a legitimate measured value for three of these metrics.
"""

from __future__ import annotations

import pytest

from app.analysis.metrics import compute_swing_metrics
from app.analysis.phases import segment_swing_phases
from app.analysis.rubric import (
    CATEGORY_WEIGHTS,
    METRIC_SPECS,
    MIN_SCORED_CATEGORIES,
    RUBRIC_VERSION,
    BandDirection,
    MetricBand,
    bands_for,
    make_band,
)
from app.analysis.scoring import (
    build_scorecard,
    overall_from_categories,
    score_category,
    score_from_band,
    score_metric,
)
from app.models.enums import MetricUnit, MetricVerdict, ScoreCategory, ShotType
from app.models.responses import CategoryScore, SwingMetrics

from tests.unit.analysis.synthetic_swing import (
    RIGHT_HANDED,
    build_contact,
    build_sequence,
)

# Every metric set to a value comfortably inside the forehand_topspin band.
IN_BAND_TOPSPIN: dict[str, float] = {
    "shoulder_hip_separation_deg": 52.0,
    "shoulder_turn_deg": 95.0,
    "hip_rotation_deg": 47.0,
    "elbow_angle_at_contact_deg": 140.0,
    "wrist_lag_deg": 30.0,
    "contact_height_ratio": 0.90,
    "contact_point_forward_tu": 0.42,
    "peak_hand_speed_tu_s": 9.0,
    "swing_path_angle_deg": 25.0,
    "swing_plane_deviation_tu": 0.04,
    "knee_flexion_min_deg": 150.0,
    "follow_through_height_tu": 0.60,
    "head_stillness_tu": 0.03,
    "tempo_ratio": 2.2,
}

TWO_SIDED_BAND = MetricBand(
    ideal_min=10.0,
    ideal_max=20.0,
    hard_min=0.0,
    hard_max=30.0,
    weight=1.0,
    category=ScoreCategory.CONTACT,
    unit=MetricUnit.DEGREES,
)
LOWER_IS_BETTER_BAND = MetricBand(
    ideal_min=0.0,
    ideal_max=0.06,
    hard_min=0.0,
    hard_max=0.12,
    weight=1.0,
    category=ScoreCategory.BALANCE,
    unit=MetricUnit.TORSO_UNITS,
    direction=BandDirection.LOWER_IS_BETTER,
)


def in_band_metrics() -> SwingMetrics:
    return SwingMetrics(**IN_BAND_TOPSPIN)


# --- the ramp ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_score", "expected_verdict"),
    [
        (0.0, 0.0, MetricVerdict.LOW),  # at hard_min
        (5.0, 50.0, MetricVerdict.LOW),  # midway up the lower shoulder
        (10.0, 100.0, MetricVerdict.IDEAL),  # at ideal_min
        (15.0, 100.0, MetricVerdict.IDEAL),  # midpoint
        (20.0, 100.0, MetricVerdict.IDEAL),  # at ideal_max
        (25.0, 50.0, MetricVerdict.HIGH),  # midway down the upper shoulder
        (30.0, 0.0, MetricVerdict.HIGH),  # at hard_max
        (-99.0, 0.0, MetricVerdict.LOW),  # far below
        (99.0, 0.0, MetricVerdict.HIGH),  # far above
    ],
)
def test_two_sided_ramp_boundaries(
    value: float, expected_score: float, expected_verdict: MetricVerdict
) -> None:
    score, verdict = score_from_band(value, TWO_SIDED_BAND)
    assert score == pytest.approx(expected_score)
    assert verdict == expected_verdict


def test_lower_is_better_never_scores_low() -> None:
    """A perfectly still head must not be coached to move more."""
    score, verdict = score_from_band(0.0, LOWER_IS_BETTER_BAND)
    assert score == 100.0
    assert verdict == MetricVerdict.IDEAL
    assert score_from_band(-0.5, LOWER_IS_BETTER_BAND) == (100.0, MetricVerdict.IDEAL)
    assert score_from_band(0.09, LOWER_IS_BETTER_BAND)[1] == MetricVerdict.HIGH
    assert score_from_band(0.12, LOWER_IS_BETTER_BAND) == (0.0, MetricVerdict.HIGH)


def test_none_is_unavailable_and_scores_none_not_zero() -> None:
    score, verdict = score_from_band(None, TWO_SIDED_BAND)
    assert score is None
    assert verdict == MetricVerdict.UNAVAILABLE
    # ...while a measured 0.0 on a band that reaches 0 is a real 100.
    assert score_from_band(0.0, LOWER_IS_BETTER_BAND)[0] == 100.0


def test_non_finite_values_are_unavailable_rather_than_scored() -> None:
    assert score_from_band(float("nan"), TWO_SIDED_BAND) == (None, MetricVerdict.UNAVAILABLE)
    assert score_from_band(float("inf"), TWO_SIDED_BAND) == (None, MetricVerdict.UNAVAILABLE)


def test_score_metric_carries_the_band_and_the_view_sensitivity_flag() -> None:
    band = make_band("shoulder_turn_deg", 80.0, 110.0)
    metric = score_metric("shoulder_turn_deg", 95.0, band)
    assert metric.name == "shoulder_turn_deg"
    assert metric.value == pytest.approx(95.0)
    assert metric.unit == MetricUnit.DEGREES
    assert metric.score_0_100 == 100.0
    assert metric.weight == band.weight
    assert (metric.ideal_min, metric.ideal_max) == (80.0, 110.0)
    assert metric.category == ScoreCategory.PREPARATION
    assert metric.view_sensitive is True

    unavailable = score_metric("shoulder_turn_deg", None, band)
    assert unavailable.value is None
    assert unavailable.score_0_100 is None
    assert unavailable.verdict == MetricVerdict.UNAVAILABLE


# --- category aggregation ----------------------------------------------------


def test_unavailable_metrics_are_excluded_and_weight_redistributed_within_category() -> None:
    bands = bands_for(ShotType.FOREHAND_TOPSPIN)
    scored = [
        score_metric("knee_flexion_min_deg", 150.0, bands["knee_flexion_min_deg"]),
        score_metric("head_stillness_tu", None, bands["head_stillness_tu"]),
    ]
    category = score_category(ScoreCategory.BALANCE, scored)
    assert category.metrics_available == 1
    assert category.metrics_total == 2
    # The one available metric carries the whole category: 0.55 renormalized to 1.0.
    assert category.score_0_100 == pytest.approx(100.0)


def test_a_category_with_no_available_metric_is_none_not_zero() -> None:
    bands = bands_for(ShotType.FOREHAND_TOPSPIN)
    scored = [
        score_metric("knee_flexion_min_deg", None, bands["knee_flexion_min_deg"]),
        score_metric("head_stillness_tu", None, bands["head_stillness_tu"]),
    ]
    category = score_category(ScoreCategory.BALANCE, scored)
    assert category.score_0_100 is None
    assert category.metrics_available == 0
    assert category.metrics_total == 2
    assert category.metric_names == ["knee_flexion_min_deg", "head_stillness_tu"]


def test_category_score_is_the_renormalized_weighted_mean() -> None:
    bands = bands_for(ShotType.FOREHAND_TOPSPIN)
    # knee 0.55 at 100, head 0.45 at 0 -> 55.0
    scored = [
        score_metric("knee_flexion_min_deg", 150.0, bands["knee_flexion_min_deg"]),
        score_metric("head_stillness_tu", 0.12, bands["head_stillness_tu"]),
    ]
    assert score_category(ScoreCategory.BALANCE, scored).score_0_100 == pytest.approx(55.0)


def test_overall_needs_the_minimum_number_of_scored_categories() -> None:
    def category(name: ScoreCategory, score: float | None) -> CategoryScore:
        return CategoryScore(
            category=name,
            score_0_100=score,
            weight=CATEGORY_WEIGHTS[name],
            metric_names=[],
            metrics_available=1 if score is not None else 0,
            metrics_total=1,
        )

    order = list(ScoreCategory)
    two = [category(name, 80.0 if index < 2 else None) for index, name in enumerate(order)]
    assert overall_from_categories(two) is None

    three = [
        category(name, 80.0 if index < MIN_SCORED_CATEGORIES else None)
        for index, name in enumerate(order)
    ]
    assert overall_from_categories(three) == pytest.approx(80.0)


def test_overall_renormalizes_over_scored_categories_and_never_counts_none_as_zero() -> None:
    def category(name: ScoreCategory, score: float | None) -> CategoryScore:
        return CategoryScore(
            category=name,
            score_0_100=score,
            weight=CATEGORY_WEIGHTS[name],
            metric_names=[],
            metrics_available=1 if score is not None else 0,
            metrics_total=1,
        )

    categories = [
        category(ScoreCategory.PREPARATION, 60.0),  # weight 0.20
        category(ScoreCategory.CONTACT, 90.0),  # weight 0.30
        category(ScoreCategory.SWING_PATH, 70.0),  # weight 0.25
        category(ScoreCategory.BALANCE, None),
        category(ScoreCategory.FOLLOW_THROUGH, None),
    ]
    expected = (0.20 * 60.0 + 0.30 * 90.0 + 0.25 * 70.0) / 0.75
    assert overall_from_categories(categories) == pytest.approx(round(expected, 1))
    # If None had been counted as 0 the answer would be the much lower:
    assert overall_from_categories(categories) != pytest.approx(
        round(0.20 * 60.0 + 0.30 * 90.0 + 0.25 * 70.0, 1)
    )


# --- end to end --------------------------------------------------------------


def test_a_fully_populated_metric_set_produces_a_full_scorecard() -> None:
    """The anti-vacuity test: every unit-level assertion passes while broken."""
    card = build_scorecard(in_band_metrics(), ShotType.FOREHAND_TOPSPIN)
    assert card.overall_score is not None
    assert card.overall_score == pytest.approx(100.0)
    assert len(card.categories) == len(ScoreCategory)
    for category in card.categories:
        assert category.score_0_100 is not None, category.category
        assert category.metrics_available == category.metrics_total
    assert len(card.metrics) == len(METRIC_SPECS)
    assert card.rubric_version == RUBRIC_VERSION


def test_an_unbanded_shot_type_still_scores_three_categories() -> None:
    """The BASE_BANDS product decision, end to end."""
    card = build_scorecard(in_band_metrics(), ShotType.BACKHAND_TWO_HANDED)
    scored = [item for item in card.categories if item.score_0_100 is not None]
    assert len(scored) == MIN_SCORED_CATEGORIES
    assert card.overall_score is not None
    blank = {item.category for item in card.categories if item.score_0_100 is None}
    assert blank == {ScoreCategory.CONTACT, ScoreCategory.FOLLOW_THROUGH}
    for item in card.categories:
        if item.category in blank:
            assert item.metrics_total == 0  # honest: there are no bands, not 5 missing


@pytest.mark.parametrize("missing", sorted(IN_BAND_TOPSPIN))
def test_one_missing_metric_only_changes_its_category_by_renormalization(
    missing: str,
) -> None:
    metrics = in_band_metrics().model_copy(update={missing: None})
    card = build_scorecard(metrics, ShotType.FOREHAND_TOPSPIN)
    by_name = {item.name: item for item in card.metrics}
    assert by_name[missing].score_0_100 is None
    assert by_name[missing].verdict == MetricVerdict.UNAVAILABLE

    category = METRIC_SPECS[missing].category
    by_category = {item.category: item for item in card.categories}
    siblings = [
        name
        for name, spec in METRIC_SPECS.items()
        if spec.category == category and name != missing
    ]
    if siblings:
        # Every survivor is at 100, so the renormalized category is still 100.
        assert by_category[category].score_0_100 == pytest.approx(100.0)
        assert by_category[category].metrics_available == len(siblings)
    else:
        assert by_category[category].score_0_100 is None
        assert by_category[category].metrics_available == 0
    # Other categories are untouched.
    for item in card.categories:
        if item.category != category:
            assert item.score_0_100 == pytest.approx(100.0)


def test_an_empty_balance_category_does_not_drag_the_overall_score_down() -> None:
    metrics = in_band_metrics().model_copy(
        update={"knee_flexion_min_deg": None, "head_stillness_tu": None}
    )
    card = build_scorecard(metrics, ShotType.FOREHAND_TOPSPIN)
    balance = next(item for item in card.categories if item.category == ScoreCategory.BALANCE)
    assert balance.score_0_100 is None
    assert balance.metrics_available == 0
    assert card.overall_score == pytest.approx(100.0)  # not 85.0


def test_an_all_none_metric_set_returns_a_populated_but_unscored_scorecard() -> None:
    card = build_scorecard(SwingMetrics(), ShotType.FOREHAND_TOPSPIN)
    assert card.overall_score is None
    assert len(card.categories) == len(ScoreCategory)
    assert len(card.metrics) == len(METRIC_SPECS)
    assert all(item.score_0_100 is None for item in card.categories)
    assert all(item.score_0_100 is None for item in card.metrics)
    assert all(item.verdict == MetricVerdict.UNAVAILABLE for item in card.metrics)


def test_phases_none_deletes_preparation_and_balance_end_to_end() -> None:
    """Pins the phase-dependency map as a tested contract, not a claim."""
    seq = build_sequence()
    contact = build_contact()
    with_phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    scored_metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, None)
    card = build_scorecard(scored_metrics, ShotType.FOREHAND_TOPSPIN)

    by_category = {item.category: item for item in card.categories}
    assert with_phases is not None  # the fixture really is segmentable
    assert by_category[ScoreCategory.PREPARATION].score_0_100 is None
    assert by_category[ScoreCategory.BALANCE].score_0_100 is None
    assert by_category[ScoreCategory.PREPARATION].metrics_available == 0
    assert by_category[ScoreCategory.BALANCE].metrics_available == 0
    scored = [item for item in card.categories if item.score_0_100 is not None]
    assert len(scored) == MIN_SCORED_CATEGORIES


def test_zero_is_scored_as_a_measurement_not_as_missing() -> None:
    """0.0 is legitimate for these three metrics and must not read unavailable."""
    metrics = in_band_metrics().model_copy(
        update={
            "contact_point_forward_tu": 0.0,
            "swing_path_angle_deg": 0.0,
            "head_stillness_tu": 0.0,
        }
    )
    card = build_scorecard(metrics, ShotType.FOREHAND_TOPSPIN)
    by_name = {item.name: item for item in card.metrics}
    assert by_name["contact_point_forward_tu"].verdict == MetricVerdict.LOW
    assert by_name["contact_point_forward_tu"].score_0_100 is not None
    assert by_name["swing_path_angle_deg"].verdict == MetricVerdict.LOW
    assert by_name["head_stillness_tu"].verdict == MetricVerdict.IDEAL
    assert by_name["head_stillness_tu"].score_0_100 == 100.0


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_weights_are_conserved_under_arbitrary_availability_masks(
    shot_type: ShotType,
) -> None:
    """Property-style: renormalized weights sum to 1.0, or the category is None."""
    names = sorted(bands_for(shot_type))
    for mask in range(2 ** len(names)):
        update = {
            name: (IN_BAND_TOPSPIN[name] if (mask >> index) & 1 else None)
            for index, name in enumerate(names)
        }
        card = build_scorecard(SwingMetrics(**update), shot_type)
        for category in card.categories:
            available = [
                item
                for item in card.metrics
                if item.category == category.category and item.score_0_100 is not None
            ]
            if not available:
                assert category.score_0_100 is None
                continue
            total = sum(item.weight for item in available)
            assert total > 0.0
            assert sum(item.weight / total for item in available) == pytest.approx(1.0)
        scored = [item for item in card.categories if item.score_0_100 is not None]
        if len(scored) < MIN_SCORED_CATEGORIES:
            assert card.overall_score is None
        else:
            assert card.overall_score is not None
        if mask > 200:  # 2**14 masks is far more than this needs to prove
            break
