"""Stage 15a: THE scoring rubric (PURE, PIPELINE.md 4.4 row 15).

Tables and derivations only -- no arrays, no I/O. ``analysis/scoring.py`` turns
these bands into a ``Scorecard``; ``feedback/references.py`` projects them down
to the ``(minimum, maximum)`` pairs its deviation comparison needs.

WHY THIS TABLE EXISTS AT ALL
----------------------------
``feedback/references.py`` used to key its table by ``"topspin" | "slice" |
"flat"`` while the wire enum is ``ShotType`` (``"forehand_topspin"``, ...). The
two key spaces shared NO member, so ``reference_ranges_for()`` returned ``{}``
for every one of the seven ``ShotType`` values and any scorecard built on it was
empty on every clip ever uploaded -- silently, because ``reference_ranges_for``
degrades to ``{}`` rather than raising and an all-unavailable ``Scorecard`` is a
legal ``Scorecard``. This module is now the SINGLE SOURCE OF TRUTH, keyed by
``ShotType``, and ``REFERENCE_RANGES`` is a derived projection of it so the two
cannot drift apart again.

EVERY NUMBER BELOW IS A PLACEHOLDER
-----------------------------------
The ideal bands are inherited verbatim from the original reference table, where
each row carried ``PLACEHOLDER - needs empirical tuning``. None of them has been
validated against this pipeline's own normalization (torso units, image-plane
angles, this contact definition). The ``hard_min``/``hard_max`` derivation is
newer still: "one full ideal-band-width outside the band, clamped at a physical
limit where one exists" is a defensible SHAPE, not a measurement.

TRACKED FOLLOW-UP -- REAL BANDS FOR FOUR SHOT TYPES
---------------------------------------------------
Only ``forehand_topspin`` and ``forehand_slice`` have authored bands.
``backhand_one_handed``, ``backhand_two_handed``, ``serve`` and ``volley`` fall
back to :data:`BASE_BANDS`, which contains ONLY the metrics whose bands already
agreed across the three original archetypes -- so applying them to an unbanded
shot type asserts nothing that the existing tables did not already assert. The
cost is stated rather than hidden: an unbanded shot type scores ``preparation``,
``swing_path`` (2 of 3) and ``balance``, and scores ``contact`` and
``follow_through`` as ``None``, because nobody has yet written down what a good
one-handed-backhand contact height is. **Authoring real bands for those four
shot types is the committed follow-up; this fallback is the launch position, not
the answer.**
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.metrics import METRIC_VIEW_SENSITIVE
from app.models.enums import MetricUnit, ScoreCategory, ShotType

#: Persisted on every stored analysis row, so it is a contract string and
#: changing it later is not free.
#:
#: `v0_placeholder`, NOT `rubric_v1`, and this CONTRADICTS `PIPELINE.md:1013`
#: ("rubric_version stays rubric_v1"). The contradiction is deliberate and is
#: recorded in PIPELINE.md's change log rather than made silently. Every band
#: this rubric was derived from is individually marked
#: `PLACEHOLDER - needs empirical tuning` (`feedback/references.py` carries 49
#: such markers, and its own docstring says they must not drive anything
#: user-visible until they are tuned). Shipping them under `rubric_v1` would
#: assert a validated rubric that does not exist. Naming the placeholder makes
#: the eventual tuning a version BUMP, visible in every stored row, rather than
#: a silent change to what `rubric_v1` means.
RUBRIC_VERSION: Final[str] = "rubric_v0_placeholder"

# Emitted only when at least this many of the five categories have a real score.
# A clip where only `contact` is measurable otherwise produces an "overall swing
# assessment" that is in fact one category out of five, renormalized to weight
# 1.0 -- impeccable arithmetic, misleading number. 3 is chosen because losing
# phase segmentation costs exactly two categories, so the threshold admits the
# common degradation and rejects the pathological one. NEW NUMBER, no empirical
# basis.
MIN_SCORED_CATEGORIES: Final[int] = 3


class BandDirection(StrEnum):
    """Whether both sides of a band are faults, or only the upper one."""

    TWO_SIDED = "two_sided"
    # For metrics whose band starts at 0.0 and where 0.0 is PERFECT, not "low":
    # a perfectly still head must not score `low` and generate coaching advice
    # to move it more.
    LOWER_IS_BETTER = "lower_is_better"


class MetricSpec(BaseModel):
    """What is true of a metric regardless of which shot was played.

    Category, weight, unit and direction do not vary by shot type, so they live
    here once instead of being repeated in every band table -- which is how the
    original three tables drifted into disagreeing about what they contained.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: ScoreCategory
    weight: float = Field(gt=0.0, description="Within its category, pre-renormalization.")
    unit: MetricUnit
    direction: BandDirection = BandDirection.TWO_SIDED
    physical_min: float | None = Field(
        default=None, description="Hard physical floor, e.g. 0 for a distance."
    )
    physical_max: float | None = Field(
        default=None, description="Hard physical ceiling, e.g. 180 for a joint angle."
    )


class MetricBand(BaseModel):
    """One metric's coaching band plus the ramp shoulders the score decays over."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ideal_min: float
    ideal_max: float
    hard_min: float
    hard_max: float
    weight: float = Field(gt=0.0)
    category: ScoreCategory
    unit: MetricUnit
    direction: BandDirection = BandDirection.TWO_SIDED


# --- what each metric IS -----------------------------------------------------

METRIC_SPECS: Final[dict[str, MetricSpec]] = {
    # preparation
    "shoulder_hip_separation_deg": MetricSpec(
        category=ScoreCategory.PREPARATION, weight=0.40, unit=MetricUnit.DEGREES
    ),
    "shoulder_turn_deg": MetricSpec(
        category=ScoreCategory.PREPARATION,
        weight=0.30,
        unit=MetricUnit.DEGREES,
        physical_min=0.0,
    ),
    "hip_rotation_deg": MetricSpec(
        category=ScoreCategory.PREPARATION,
        weight=0.30,
        unit=MetricUnit.DEGREES,
        physical_min=0.0,
    ),
    # contact
    "contact_height_ratio": MetricSpec(
        category=ScoreCategory.CONTACT, weight=0.25, unit=MetricUnit.RATIO
    ),
    "contact_point_forward_tu": MetricSpec(
        category=ScoreCategory.CONTACT, weight=0.25, unit=MetricUnit.TORSO_UNITS
    ),
    "elbow_angle_at_contact_deg": MetricSpec(
        category=ScoreCategory.CONTACT,
        weight=0.20,
        unit=MetricUnit.DEGREES,
        physical_min=0.0,
        physical_max=180.0,
    ),
    "wrist_lag_deg": MetricSpec(
        category=ScoreCategory.CONTACT,
        weight=0.15,
        unit=MetricUnit.DEGREES,
        physical_min=0.0,
        physical_max=180.0,
    ),
    "peak_hand_speed_tu_s": MetricSpec(
        category=ScoreCategory.CONTACT,
        weight=0.15,
        unit=MetricUnit.TORSO_UNITS_PER_SEC,
        physical_min=0.0,
    ),
    # swing_path
    "swing_path_angle_deg": MetricSpec(
        category=ScoreCategory.SWING_PATH, weight=0.45, unit=MetricUnit.DEGREES
    ),
    "swing_plane_deviation_tu": MetricSpec(
        category=ScoreCategory.SWING_PATH,
        weight=0.35,
        unit=MetricUnit.TORSO_UNITS,
        direction=BandDirection.LOWER_IS_BETTER,
        physical_min=0.0,
    ),
    "tempo_ratio": MetricSpec(
        category=ScoreCategory.SWING_PATH,
        weight=0.20,
        unit=MetricUnit.RATIO,
        physical_min=0.0,
    ),
    # balance
    "knee_flexion_min_deg": MetricSpec(
        category=ScoreCategory.BALANCE,
        weight=0.55,
        unit=MetricUnit.DEGREES,
        physical_min=0.0,
        physical_max=180.0,
    ),
    "head_stillness_tu": MetricSpec(
        category=ScoreCategory.BALANCE,
        weight=0.45,
        unit=MetricUnit.TORSO_UNITS,
        direction=BandDirection.LOWER_IS_BETTER,
        physical_min=0.0,
    ),
    # follow_through -- the ONLY metric in its category, so there is no
    # redundancy: if it is None the whole category is None.
    "follow_through_height_tu": MetricSpec(
        category=ScoreCategory.FOLLOW_THROUGH, weight=1.00, unit=MetricUnit.TORSO_UNITS
    ),
}

# Deliberately NOT scored, with the reason attached to each, because "why is
# this metric missing from the scorecard" is otherwise a recurring question.
EXCLUDED_METRICS: Final[dict[str, str]] = {
    "weight_transfer_tu": (
        "structurally unmeasurable: Stage 7 pins the mid-hip at the origin, so "
        "its displacement is identically zero on every clip (metrics.py:109-118)"
    ),
    "balance_sway_tu": (
        "structurally unmeasurable, same cause as weight_transfer_tu"
    ),
    "takeback_displacement_tu": (
        "Stage 13 declines to define it (metrics.py:636-638); it is permanently None"
    ),
    "wrist_separation_at_contact_tu": (
        "a Stage 14 CLASSIFICATION input, not a coachable band"
    ),
    "ball_speed_mph": (
        "a physical measurement, never a scored metric; MetricUnit has no mph "
        "member by design"
    ),
}

# A metric that is structurally unmeasurable is not an unavailable measurement;
# it is not a metric of this pipeline yet. Banding the first three would put
# three permanent `unavailable` rows in every scorecard ever produced and would
# inflate `metrics_total`, making `preparation` read "1 of 4 measured" when the
# honest statement is "1 of 3".

CATEGORY_WEIGHTS: Final[dict[ScoreCategory, float]] = {
    # NONE of these is in PIPELINE.md. `contact` is highest as the
    # best-measured and most directly coachable group; `follow_through` is
    # lowest because it is a single metric. Coaching judgment, asserted by an
    # engineer, not a measurement.
    ScoreCategory.PREPARATION: 0.20,
    ScoreCategory.CONTACT: 0.30,
    ScoreCategory.SWING_PATH: 0.25,
    ScoreCategory.BALANCE: 0.15,
    ScoreCategory.FOLLOW_THROUGH: 0.10,
}


# --- band construction -------------------------------------------------------


def hard_bounds(
    ideal_min: float, ideal_max: float, spec: MetricSpec
) -> tuple[float, float]:
    """Where the score reaches 0: one full ideal-band-width outside the band.

    Clamped at the metric's physical limit where it has one, so a band that
    already sits against 0 deg or 180 deg does not get a shoulder in impossible
    territory. This is a SHAPE, not a measurement -- PIPELINE.md requires
    ``hard_min``/``hard_max`` but never supplies any.
    """
    width = float(ideal_max) - float(ideal_min)
    low = float(ideal_min) - width
    high = float(ideal_max) + width
    if spec.physical_min is not None:
        low = max(low, float(spec.physical_min))
    if spec.physical_max is not None:
        high = min(high, float(spec.physical_max))
    return low, high


def make_band(name: str, ideal_min: float, ideal_max: float) -> MetricBand:
    """Build one band from the metric's spec plus its shot-specific ideal range."""
    spec = METRIC_SPECS[name]
    low, high = hard_bounds(ideal_min, ideal_max, spec)
    return MetricBand(
        ideal_min=float(ideal_min),
        ideal_max=float(ideal_max),
        hard_min=low,
        hard_max=high,
        weight=spec.weight,
        category=spec.category,
        unit=spec.unit,
        direction=spec.direction,
    )


def _table(ranges: dict[str, tuple[float, float]]) -> dict[str, MetricBand]:
    return {name: make_band(name, low, high) for name, (low, high) in ranges.items()}


# --- the tables --------------------------------------------------------------

# Carried over verbatim from the original `topspin` archetype.
_FOREHAND_TOPSPIN: Final[dict[str, tuple[float, float]]] = {
    "shoulder_hip_separation_deg": (40.0, 65.0),
    "shoulder_turn_deg": (80.0, 110.0),
    "hip_rotation_deg": (35.0, 60.0),
    "elbow_angle_at_contact_deg": (120.0, 160.0),
    "wrist_lag_deg": (15.0, 45.0),
    "contact_height_ratio": (0.75, 1.05),
    "contact_point_forward_tu": (0.25, 0.60),
    "peak_hand_speed_tu_s": (6.0, 12.0),
    "swing_path_angle_deg": (15.0, 35.0),
    "swing_plane_deviation_tu": (0.0, 0.08),
    "knee_flexion_min_deg": (135.0, 165.0),
    "follow_through_height_tu": (0.30, 0.90),
    "head_stillness_tu": (0.0, 0.06),
    "tempo_ratio": (1.5, 3.0),
}

# Carried over verbatim from the original `slice` archetype.
_FOREHAND_SLICE: Final[dict[str, tuple[float, float]]] = {
    "shoulder_hip_separation_deg": (30.0, 50.0),
    "shoulder_turn_deg": (70.0, 100.0),
    "hip_rotation_deg": (25.0, 45.0),
    "elbow_angle_at_contact_deg": (140.0, 175.0),
    "wrist_lag_deg": (5.0, 25.0),
    "contact_height_ratio": (0.55, 0.95),
    "contact_point_forward_tu": (0.20, 0.55),
    "peak_hand_speed_tu_s": (4.0, 9.0),
    "swing_path_angle_deg": (-30.0, -8.0),
    "swing_plane_deviation_tu": (0.0, 0.07),
    "knee_flexion_min_deg": (130.0, 160.0),
    "follow_through_height_tu": (0.0, 0.45),
    "head_stillness_tu": (0.0, 0.05),
    "tempo_ratio": (1.5, 3.0),
}

# The shot-type-AGNOSTIC subset: only metrics whose bands already agreed across
# the three original archetypes (topspin / slice / flat), widened to their
# union. Everything where the archetypes disagreed is EXCLUDED rather than
# averaged -- `swing_path_angle_deg` is the clearest case, where topspin
# (15..35) and slice (-30..-8) are disjoint and any "compromise" band would
# score both a good topspin and a good slice as wrong.
_BASE: Final[dict[str, tuple[float, float]]] = {
    "shoulder_hip_separation_deg": (30.0, 65.0),
    "shoulder_turn_deg": (70.0, 110.0),
    "hip_rotation_deg": (25.0, 60.0),
    "swing_plane_deviation_tu": (0.0, 0.08),
    "tempo_ratio": (1.5, 3.0),
    "knee_flexion_min_deg": (130.0, 165.0),
    "head_stillness_tu": (0.0, 0.06),
}

BASE_BANDS: Final[dict[str, MetricBand]] = _table(_BASE)

RUBRIC_V1: Final[dict[ShotType, dict[str, MetricBand]]] = {
    ShotType.FOREHAND_TOPSPIN: _table(_FOREHAND_TOPSPIN),
    ShotType.FOREHAND_SLICE: _table(_FOREHAND_SLICE),
}

# Named explicitly so that adding a ShotType member forces a decision about what
# it scores, rather than letting it inherit the fallback unnoticed.
UNBANDED_SHOT_TYPES: Final[frozenset[ShotType]] = frozenset(
    member for member in ShotType if member not in RUBRIC_V1
)


def bands_for(shot_type: ShotType) -> dict[str, MetricBand]:
    """The band table actually applied to ``shot_type``. NEVER empty.

    An unbanded shot type -- including ``ShotType.UNKNOWN``, per PIPELINE.md:994
    -- falls back to :data:`BASE_BANDS` rather than to nothing, so a user filming
    a backhand gets a real if conservative score instead of a blank scorecard
    and a null history row.
    """
    return RUBRIC_V1.get(shot_type, BASE_BANDS)


def metric_names_for(shot_type: ShotType, category: ScoreCategory) -> list[str]:
    """Scored metric names for one category under ``shot_type``, in table order."""
    bands = bands_for(shot_type)
    return [name for name in METRIC_SPECS if name in bands and bands[name].category == category]


def is_view_sensitive(name: str) -> bool:
    """Stage 13's per-metric view-sensitivity flag (``metrics.py:89-107``)."""
    return bool(METRIC_VIEW_SENSITIVE.get(name, False))
