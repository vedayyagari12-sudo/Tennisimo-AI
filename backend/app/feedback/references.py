"""Reference ranges per shot type, and deviation comparison.

PURE: no I/O, no network, no model. Every function here is deterministic.

WARNING ON THE NUMBERS BELOW
----------------------------
Every range in ``REFERENCE_RANGES`` is a PLACEHOLDER chosen from general coaching
intuition. None of them has been validated against measured data from this
pipeline's own normalization (torso units, image-plane angles, this contact
definition). Each entry is marked individually so no reader mistakes it for a
validated threshold. They need empirical tuning against a labelled clip set
before they drive anything user-visible.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReferenceShotType(StrEnum):
    """Shot types the reference table is keyed by."""

    TOPSPIN = "topspin"
    SLICE = "slice"
    FLAT = "flat"


class DeviationDirection(StrEnum):
    LOW = "low"
    HIGH = "high"


class ReferenceRange(BaseModel):
    """Inclusive [minimum, maximum] coaching band for one metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum: float
    maximum: float


class Deviation(BaseModel):
    """One metric that fell outside its reference range, and by how much."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float
    reference_range: ReferenceRange
    direction: DeviationDirection
    magnitude: float = Field(
        ge=0.0,
        description=(
            "Absolute distance from the nearest edge of the range, "
            "in the metric's own unit."
        ),
    )


# Metric names come from the Stage 13 table (PIPELINE.md).
# `wrist_separation_at_contact_tu` is deliberately absent: it is a shot-type
# CLASSIFICATION input, not a coachable band, and it does not vary across the
# shot types keyed here. `ball_speed_mph` is absent by contract -- ball speed is
# a measurement, never a scored or referenced metric.
REFERENCE_RANGES: dict[str, dict[str, ReferenceRange]] = {
    ReferenceShotType.TOPSPIN.value: {
        "shoulder_hip_separation_deg": ReferenceRange(minimum=40.0, maximum=65.0),  # PLACEHOLDER - needs empirical tuning
        "shoulder_turn_deg": ReferenceRange(minimum=80.0, maximum=110.0),  # PLACEHOLDER - needs empirical tuning
        "hip_rotation_deg": ReferenceRange(minimum=35.0, maximum=60.0),  # PLACEHOLDER - needs empirical tuning
        "elbow_angle_at_contact_deg": ReferenceRange(minimum=120.0, maximum=160.0),  # PLACEHOLDER - needs empirical tuning
        "wrist_lag_deg": ReferenceRange(minimum=15.0, maximum=45.0),  # PLACEHOLDER - needs empirical tuning
        "contact_height_ratio": ReferenceRange(minimum=0.75, maximum=1.05),  # PLACEHOLDER - needs empirical tuning
        "contact_point_forward_tu": ReferenceRange(minimum=0.25, maximum=0.60),  # PLACEHOLDER - needs empirical tuning
        "peak_hand_speed_tu_s": ReferenceRange(minimum=6.0, maximum=12.0),  # PLACEHOLDER - needs empirical tuning
        "swing_path_angle_deg": ReferenceRange(minimum=15.0, maximum=35.0),  # PLACEHOLDER - needs empirical tuning
        "swing_plane_deviation_tu": ReferenceRange(minimum=0.0, maximum=0.08),  # PLACEHOLDER - needs empirical tuning
        "knee_flexion_min_deg": ReferenceRange(minimum=135.0, maximum=165.0),  # PLACEHOLDER - needs empirical tuning
        "weight_transfer_tu": ReferenceRange(minimum=0.10, maximum=0.40),  # PLACEHOLDER - needs empirical tuning
        "follow_through_height_tu": ReferenceRange(minimum=0.30, maximum=0.90),  # PLACEHOLDER - needs empirical tuning
        "balance_sway_tu": ReferenceRange(minimum=0.0, maximum=0.12),  # PLACEHOLDER - needs empirical tuning
        "head_stillness_tu": ReferenceRange(minimum=0.0, maximum=0.06),  # PLACEHOLDER - needs empirical tuning
        "tempo_ratio": ReferenceRange(minimum=1.5, maximum=3.0),  # PLACEHOLDER - needs empirical tuning
    },
    ReferenceShotType.SLICE.value: {
        "shoulder_hip_separation_deg": ReferenceRange(minimum=30.0, maximum=50.0),  # PLACEHOLDER - needs empirical tuning
        "shoulder_turn_deg": ReferenceRange(minimum=70.0, maximum=100.0),  # PLACEHOLDER - needs empirical tuning
        "hip_rotation_deg": ReferenceRange(minimum=25.0, maximum=45.0),  # PLACEHOLDER - needs empirical tuning
        "elbow_angle_at_contact_deg": ReferenceRange(minimum=140.0, maximum=175.0),  # PLACEHOLDER - needs empirical tuning
        "wrist_lag_deg": ReferenceRange(minimum=5.0, maximum=25.0),  # PLACEHOLDER - needs empirical tuning
        "contact_height_ratio": ReferenceRange(minimum=0.55, maximum=0.95),  # PLACEHOLDER - needs empirical tuning
        "contact_point_forward_tu": ReferenceRange(minimum=0.20, maximum=0.55),  # PLACEHOLDER - needs empirical tuning
        "peak_hand_speed_tu_s": ReferenceRange(minimum=4.0, maximum=9.0),  # PLACEHOLDER - needs empirical tuning
        "swing_path_angle_deg": ReferenceRange(minimum=-30.0, maximum=-8.0),  # PLACEHOLDER - needs empirical tuning
        "swing_plane_deviation_tu": ReferenceRange(minimum=0.0, maximum=0.07),  # PLACEHOLDER - needs empirical tuning
        "knee_flexion_min_deg": ReferenceRange(minimum=130.0, maximum=160.0),  # PLACEHOLDER - needs empirical tuning
        "weight_transfer_tu": ReferenceRange(minimum=0.10, maximum=0.40),  # PLACEHOLDER - needs empirical tuning
        "follow_through_height_tu": ReferenceRange(minimum=0.0, maximum=0.45),  # PLACEHOLDER - needs empirical tuning
        "balance_sway_tu": ReferenceRange(minimum=0.0, maximum=0.10),  # PLACEHOLDER - needs empirical tuning
        "head_stillness_tu": ReferenceRange(minimum=0.0, maximum=0.05),  # PLACEHOLDER - needs empirical tuning
        "tempo_ratio": ReferenceRange(minimum=1.5, maximum=3.0),  # PLACEHOLDER - needs empirical tuning
    },
    ReferenceShotType.FLAT.value: {
        "shoulder_hip_separation_deg": ReferenceRange(minimum=35.0, maximum=60.0),  # PLACEHOLDER - needs empirical tuning
        "shoulder_turn_deg": ReferenceRange(minimum=75.0, maximum=105.0),  # PLACEHOLDER - needs empirical tuning
        "hip_rotation_deg": ReferenceRange(minimum=30.0, maximum=55.0),  # PLACEHOLDER - needs empirical tuning
        "elbow_angle_at_contact_deg": ReferenceRange(minimum=130.0, maximum=170.0),  # PLACEHOLDER - needs empirical tuning
        "wrist_lag_deg": ReferenceRange(minimum=10.0, maximum=35.0),  # PLACEHOLDER - needs empirical tuning
        "contact_height_ratio": ReferenceRange(minimum=0.85, maximum=1.15),  # PLACEHOLDER - needs empirical tuning
        "contact_point_forward_tu": ReferenceRange(minimum=0.30, maximum=0.65),  # PLACEHOLDER - needs empirical tuning
        "peak_hand_speed_tu_s": ReferenceRange(minimum=6.5, maximum=13.0),  # PLACEHOLDER - needs empirical tuning
        "swing_path_angle_deg": ReferenceRange(minimum=0.0, maximum=14.0),  # PLACEHOLDER - needs empirical tuning
        "swing_plane_deviation_tu": ReferenceRange(minimum=0.0, maximum=0.06),  # PLACEHOLDER - needs empirical tuning
        "knee_flexion_min_deg": ReferenceRange(minimum=135.0, maximum=165.0),  # PLACEHOLDER - needs empirical tuning
        "weight_transfer_tu": ReferenceRange(minimum=0.12, maximum=0.45),  # PLACEHOLDER - needs empirical tuning
        "follow_through_height_tu": ReferenceRange(minimum=0.20, maximum=0.70),  # PLACEHOLDER - needs empirical tuning
        "balance_sway_tu": ReferenceRange(minimum=0.0, maximum=0.11),  # PLACEHOLDER - needs empirical tuning
        "head_stillness_tu": ReferenceRange(minimum=0.0, maximum=0.06),  # PLACEHOLDER - needs empirical tuning
        "tempo_ratio": ReferenceRange(minimum=1.5, maximum=3.0),  # PLACEHOLDER - needs empirical tuning
    },
}


def reference_ranges_for(shot_type: str) -> dict[str, ReferenceRange]:
    """Return the range table for ``shot_type``, or an empty table if unknown.

    An unknown shot type degrades to "no references", never an exception.
    """
    return REFERENCE_RANGES.get(str(shot_type), {})


def compare_to_reference(
    metrics: Mapping[str, float | None],
    shot_type: str,
) -> list[Deviation]:
    """Return the metrics falling outside their reference range for ``shot_type``.

    A metric whose value is ``None`` is UNAVAILABLE: it is skipped entirely and is
    never treated as zero. Metrics with no reference entry are skipped. An unknown
    shot type yields an empty list rather than raising.
    """
    ranges = reference_ranges_for(shot_type)
    if not ranges:
        return []

    deviations: list[Deviation] = []
    for name, value in metrics.items():
        if value is None:  # unavailable -- NOT zero, NOT a deviation
            continue
        band = ranges.get(name)
        if band is None:
            continue
        if value < band.minimum:
            deviations.append(
                Deviation(
                    name=name,
                    value=float(value),
                    reference_range=band,
                    direction=DeviationDirection.LOW,
                    magnitude=float(band.minimum - value),
                )
            )
        elif value > band.maximum:
            deviations.append(
                Deviation(
                    name=name,
                    value=float(value),
                    reference_range=band,
                    direction=DeviationDirection.HIGH,
                    magnitude=float(value - band.maximum),
                )
            )
    return deviations
