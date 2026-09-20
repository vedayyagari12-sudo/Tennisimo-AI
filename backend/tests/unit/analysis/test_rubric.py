"""Stage 15a unit tests: the rubric table itself.

The first test here is the one whose ABSENCE let the key-space defect survive:
nothing asserted that a ``ShotType`` member resolved to a non-empty band table,
so ``reference_ranges_for`` returning ``{}`` for all seven members passed every
test in the suite.
"""

from __future__ import annotations

import pytest

from app.analysis.metrics import METRIC_VIEW_SENSITIVE
from app.analysis.rubric import (
    BASE_BANDS,
    CATEGORY_WEIGHTS,
    EXCLUDED_METRICS,
    METRIC_SPECS,
    MIN_SCORED_CATEGORIES,
    RUBRIC_V1,
    RUBRIC_VERSION,
    UNBANDED_SHOT_TYPES,
    BandDirection,
    bands_for,
    hard_bounds,
    is_view_sensitive,
    make_band,
    metric_names_for,
)
from app.models.enums import ScoreCategory, ShotType

BANDED_SHOT_TYPES = {ShotType.FOREHAND_TOPSPIN, ShotType.FOREHAND_SLICE}


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_every_shot_type_resolves_to_a_non_empty_band_table(shot_type: ShotType) -> None:
    """THE key-space test. Parametrized so a new enum member fails until decided."""
    bands = bands_for(shot_type)
    assert bands, shot_type
    if shot_type not in BANDED_SHOT_TYPES:
        assert shot_type in UNBANDED_SHOT_TYPES
        assert bands is BASE_BANDS


def test_the_unbanded_allowlist_is_explicit_and_exactly_the_five_expected() -> None:
    assert UNBANDED_SHOT_TYPES == {
        ShotType.BACKHAND_ONE_HANDED,
        ShotType.BACKHAND_TWO_HANDED,
        ShotType.SERVE,
        ShotType.VOLLEY,
        ShotType.UNKNOWN,
    }
    assert set(RUBRIC_V1) == BANDED_SHOT_TYPES


def test_base_bands_cover_exactly_three_categories_which_clears_the_floor() -> None:
    """The BASE_BANDS product decision, asserted rather than assumed."""
    categories = {band.category for band in BASE_BANDS.values()}
    assert categories == {
        ScoreCategory.PREPARATION,
        ScoreCategory.SWING_PATH,
        ScoreCategory.BALANCE,
    }
    assert len(categories) >= MIN_SCORED_CATEGORIES
    # contact and follow_through are blank on purpose: nobody has written down
    # what a good one-handed-backhand contact height is.
    assert not metric_names_for(ShotType.SERVE, ScoreCategory.CONTACT)
    assert not metric_names_for(ShotType.SERVE, ScoreCategory.FOLLOW_THROUGH)
    # swing_path_angle_deg is excluded because topspin and slice are DISJOINT.
    assert "swing_path_angle_deg" not in BASE_BANDS


def test_base_bands_are_the_union_of_the_archetypes_they_came_from() -> None:
    """A widened band asserts less, not more. Check it did not narrow anything."""
    for name, band in BASE_BANDS.items():
        topspin = RUBRIC_V1[ShotType.FOREHAND_TOPSPIN].get(name)
        slice_band = RUBRIC_V1[ShotType.FOREHAND_SLICE].get(name)
        for source in (topspin, slice_band):
            if source is None:
                continue
            assert band.ideal_min <= source.ideal_min
            assert band.ideal_max >= source.ideal_max


def test_permanently_unmeasurable_metrics_are_excluded_from_every_table() -> None:
    for name in EXCLUDED_METRICS:
        assert name not in METRIC_SPECS
        for shot_type in ShotType:
            assert name not in bands_for(shot_type), (shot_type, name)


def test_every_band_is_ordered_and_sits_inside_its_hard_shoulders() -> None:
    for shot_type in ShotType:
        for name, band in bands_for(shot_type).items():
            assert band.hard_min <= band.ideal_min <= band.ideal_max <= band.hard_max, (
                shot_type,
                name,
            )
            assert band.weight > 0.0


def test_category_weights_cover_the_five_categories_and_sum_to_one() -> None:
    assert set(CATEGORY_WEIGHTS) == set(ScoreCategory)
    assert sum(CATEGORY_WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_within_category_weights_sum_to_one_where_the_category_is_banded(
    shot_type: ShotType,
) -> None:
    bands = bands_for(shot_type)
    for category in ScoreCategory:
        members = [band for band in bands.values() if band.category == category]
        if not members:
            continue
        # BASE_BANDS drops metrics from a category, so only a fully-banded
        # category is required to sum to 1.0.
        full = len(members) == sum(
            1 for spec in METRIC_SPECS.values() if spec.category == category
        )
        if full:
            assert sum(band.weight for band in members) == pytest.approx(1.0), category


def test_hard_bounds_are_one_band_width_out_and_clamp_at_physical_limits() -> None:
    elbow = METRIC_SPECS["elbow_angle_at_contact_deg"]
    assert hard_bounds(120.0, 160.0, elbow) == (80.0, 180.0)  # 200 clamped to 180
    wrist = METRIC_SPECS["wrist_lag_deg"]
    assert hard_bounds(15.0, 45.0, wrist) == (0.0, 75.0)  # -15 clamped to 0
    separation = METRIC_SPECS["shoulder_hip_separation_deg"]
    assert hard_bounds(40.0, 65.0, separation) == (15.0, 90.0)  # unclamped


def test_lower_is_better_is_set_exactly_where_zero_is_perfect() -> None:
    lower = {
        name
        for name, spec in METRIC_SPECS.items()
        if spec.direction == BandDirection.LOWER_IS_BETTER
    }
    assert lower == {"swing_plane_deviation_tu", "head_stillness_tu"}


def test_make_band_inherits_category_weight_and_unit_from_the_spec() -> None:
    band = make_band("hip_rotation_deg", 30.0, 50.0)
    spec = METRIC_SPECS["hip_rotation_deg"]
    assert band.category == spec.category
    assert band.weight == spec.weight
    assert band.unit == spec.unit
    assert (band.hard_min, band.hard_max) == (10.0, 70.0)


def test_view_sensitivity_is_read_from_stage_thirteen_not_redeclared() -> None:
    for name in METRIC_SPECS:
        assert is_view_sensitive(name) == METRIC_VIEW_SENSITIVE[name]
    assert is_view_sensitive("not_a_metric") is False


def test_rubric_version_is_one_shared_constant() -> None:
    from app.models.responses import Scorecard

    assert RUBRIC_VERSION == Scorecard.model_fields["rubric_version"].default


def test_rubric_version_names_itself_a_placeholder() -> None:
    """Every band under this version is still marked PLACEHOLDER.

    Pinned as a literal, not derived, because the point of the string is that a
    human decided it. Bumping it when the bands are actually tuned should
    require editing this assertion -- that is the reminder.

    Deliberately contradicts PIPELINE.md:1013 ("rubric_version stays
    rubric_v1"); the divergence is recorded in PIPELINE.md's change log.
    """
    assert RUBRIC_VERSION == "rubric_v0_placeholder"
