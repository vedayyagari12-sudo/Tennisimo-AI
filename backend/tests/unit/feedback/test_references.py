"""Unit tests for app/feedback/references.py -- pure, synthetic metrics only."""

from __future__ import annotations

import pytest

from app.feedback.references import (
    REFERENCE_RANGES,
    DeviationDirection,
    ReferenceShotType,
    compare_to_reference,
    reference_ranges_for,
)


def test_table_has_exactly_the_three_shot_types() -> None:
    assert set(REFERENCE_RANGES) == {"topspin", "slice", "flat"}
    assert set(REFERENCE_RANGES) == {t.value for t in ReferenceShotType}


def test_every_range_is_ordered_and_non_empty() -> None:
    for shot_type, table in REFERENCE_RANGES.items():
        assert table, shot_type
        for name, band in table.items():
            assert band.minimum <= band.maximum, f"{shot_type}.{name}"


def test_ball_speed_is_never_a_reference_metric() -> None:
    for table in REFERENCE_RANGES.values():
        assert not any("mph" in name or "ball_speed" in name for name in table)


def test_in_range_metrics_produce_no_deviations() -> None:
    metrics = {
        "shoulder_hip_separation_deg": 50.0,
        "contact_height_ratio": 0.90,
        "swing_path_angle_deg": 25.0,
    }
    assert compare_to_reference(metrics, "topspin") == []


def test_boundary_values_are_in_range() -> None:
    band = REFERENCE_RANGES["topspin"]["shoulder_hip_separation_deg"]
    metrics = {"shoulder_hip_separation_deg": band.minimum}
    assert compare_to_reference(metrics, "topspin") == []
    metrics = {"shoulder_hip_separation_deg": band.maximum}
    assert compare_to_reference(metrics, "topspin") == []


def test_below_range_metric_is_reported_low_with_magnitude() -> None:
    band = REFERENCE_RANGES["topspin"]["shoulder_hip_separation_deg"]
    value = band.minimum - 6.5
    deviations = compare_to_reference(
        {"shoulder_hip_separation_deg": value}, "topspin"
    )
    assert len(deviations) == 1
    dev = deviations[0]
    assert dev.name == "shoulder_hip_separation_deg"
    assert dev.value == pytest.approx(value)
    assert dev.direction is DeviationDirection.LOW
    assert dev.magnitude == pytest.approx(6.5)
    assert dev.reference_range == band


def test_above_range_metric_is_reported_high_with_magnitude() -> None:
    band = REFERENCE_RANGES["flat"]["swing_path_angle_deg"]
    value = band.maximum + 3.0
    deviations = compare_to_reference({"swing_path_angle_deg": value}, "flat")
    assert len(deviations) == 1
    dev = deviations[0]
    assert dev.direction is DeviationDirection.HIGH
    assert dev.magnitude == pytest.approx(3.0)


def test_negative_range_slice_path_angle() -> None:
    # Slice expects a downward path; -18 deg is inside the band, +5 is above it.
    assert compare_to_reference({"swing_path_angle_deg": -18.0}, "slice") == []
    high = compare_to_reference({"swing_path_angle_deg": 5.0}, "slice")
    assert [d.direction for d in high] == [DeviationDirection.HIGH]


def test_none_valued_metric_is_skipped_and_never_treated_as_zero() -> None:
    # 0.0 would be below every one of these bands; None must not behave that way.
    metrics: dict[str, float | None] = {
        "shoulder_hip_separation_deg": None,
        "knee_flexion_min_deg": None,
        "peak_hand_speed_tu_s": None,
    }
    assert compare_to_reference(metrics, "topspin") == []

    zeroed = {name: 0.0 for name in metrics}
    assert len(compare_to_reference(zeroed, "topspin")) == 3


def test_metric_without_a_reference_entry_is_skipped() -> None:
    assert compare_to_reference({"wrist_separation_at_contact_tu": 0.2}, "topspin") == []
    assert compare_to_reference({"not_a_metric": 999.0}, "topspin") == []


def test_unknown_shot_type_degrades_to_empty_list() -> None:
    metrics = {"shoulder_hip_separation_deg": 5.0}
    assert compare_to_reference(metrics, "kick_serve") == []
    assert compare_to_reference(metrics, "") == []
    assert reference_ranges_for("kick_serve") == {}


def test_shot_type_enum_member_is_accepted() -> None:
    metrics = {"shoulder_hip_separation_deg": 5.0}
    assert len(compare_to_reference(metrics, ReferenceShotType.TOPSPIN)) == 1


def test_mixed_metrics_report_only_the_offenders() -> None:
    metrics: dict[str, float | None] = {
        "shoulder_hip_separation_deg": 50.0,  # in range
        "contact_height_ratio": 0.10,  # below
        "peak_hand_speed_tu_s": 20.0,  # above
        "knee_flexion_min_deg": None,  # unavailable
    }
    deviations = compare_to_reference(metrics, "topspin")
    assert {d.name: d.direction for d in deviations} == {
        "contact_height_ratio": DeviationDirection.LOW,
        "peak_hand_speed_tu_s": DeviationDirection.HIGH,
    }
