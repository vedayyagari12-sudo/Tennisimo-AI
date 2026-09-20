"""Reference ranges per shot type, and deviation comparison.

PURE: no I/O, no network, no model. Every function here is deterministic.

``REFERENCE_RANGES`` IS A DERIVED PROJECTION, NOT A TABLE
---------------------------------------------------------
It used to be a hand-written table keyed ``"topspin" | "slice" | "flat"``. The
wire enum is ``ShotType`` (``"forehand_topspin"``, ``"backhand_two_handed"``,
...), and the two key spaces shared NO member -- so
``reference_ranges_for(ShotType.FOREHAND_TOPSPIN.value)`` returned ``{}``, as did
every other one of the seven members, and every consumer degraded silently to
"no references" rather than raising.

It is now built at import time from ``app.analysis.rubric.RUBRIC_V1`` by taking
each band's ``(ideal_min, ideal_max)`` and keying on ``ShotType``. One source of
truth; the two tables cannot drift apart because there is only one table.
``reference_ranges_for`` and ``compare_to_reference`` keep their signatures and
their ``str`` key type, so no caller changes.

WARNING ON THE NUMBERS
----------------------
Every band is still a PLACEHOLDER chosen from general coaching intuition, now
carried in ``app/analysis/rubric.py`` where the warning is repeated. None has
been validated against this pipeline's own normalization (torso units,
image-plane angles, this contact definition).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.rubric import bands_for
from app.models.enums import ShotType


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
# CLASSIFICATION input, not a coachable band. `ball_speed_mph` is absent by
# contract -- ball speed is a measurement, never a scored or referenced metric.
# `weight_transfer_tu` and `balance_sway_tu` are absent too: they used to carry
# bands here, but the metrics are permanently None (Stage 7 pins the mid-hip at
# the origin), so those bands were unreachable code that read as evidence the
# metrics worked.
REFERENCE_RANGES: dict[str, dict[str, ReferenceRange]] = {
    shot_type.value: {
        name: ReferenceRange(minimum=band.ideal_min, maximum=band.ideal_max)
        for name, band in bands_for(shot_type).items()
    }
    for shot_type in ShotType
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
