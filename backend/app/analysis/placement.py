"""Depth and width zones per landing, filters, and shot-map aggregates (6.2).

PURE: stdlib and numpy. Zone assignment is honest: each landing gets depth
probabilities from a normal CDF (``math.erf``) on its along-court sigma, and
aggregates SUM those probabilities, so a blurry far-baseline ball never
counts as a whole "deep". ``depth_zone`` is the point-estimate zone;
``depth_confident`` is False when the 95 % range crosses a zone edge.

Decisions taken here:

* Depth is distance from the net in the bounce's own half. Probability mass
  short of the service line, including mass on the far side of the net, is
  ``short``, so the four probabilities sum to 1.
* ``ShotLanding`` gains ``target``: the wire landing carries it even when
  ``call`` is None (brief G21: no call without 8 taps).
* A landing whose call is None counts toward no verdict; ``too_close`` is
  never folded into ``in``.
* ``ServeZone`` is re-exported (design 6.5) but not assigned: neither
  ``ShotLanding`` nor the wire landing has a field for it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.analysis.court_model import (
    DEPTH_ZONE_EDGES_M,
    CourtEnd,
    DepthZone,
    WidthZone,
    depth_zone_at,
    width_zone_at,
)
from app.analysis.line_calls import LINE_CALL_Z, BouncePoint, LineCall
from app.models.court import LineCallTarget, LineVerdict, ServeZone

__all__ = [
    "PLACEMENT_VERSION",
    "DepthZone",
    "ServeZone",
    "ShotLanding",
    "ShotMapFilter",
    "ShotMapSummary",
    "WidthZone",
    "classify_landing",
    "depth_zone_probabilities",
    "filter_landings",
    "summarize_shot_map",
]

PLACEMENT_VERSION: Final[str] = "placement_v1"
#: 6.2: "the 95 % range" for ``depth_confident``.
DEPTH_CONFIDENCE_Z: Final[float] = LINE_CALL_Z
_DEPTH_ORDER: Final[tuple[DepthZone, ...]] = (DepthZone.SHORT, DepthZone.MID, DepthZone.DEEP, DepthZone.BEYOND)


@dataclass(frozen=True, eq=False)
class ShotLanding:
    bounce: BouncePoint
    #: None when the calibration does not allow calls (brief G21).
    call: LineCall | None
    hitter_end: CourtEnd | None
    shot_type: str | None
    is_serve: bool
    target: LineCallTarget
    depth_zone: DepthZone
    depth_confident: bool
    depth_probabilities: Mapping[DepthZone, float]
    width_zone: WidthZone
    #: Where the hitter stood (court metres), the start of the shot's path on
    #: the map. None when not measured; never guessed from the hitter's end.
    hit_xy_m: tuple[float, float] | None = None


@dataclass(frozen=True)
class ShotMapFilter:
    hitter_end: CourtEnd | None = None
    shot_type: str | None = None
    verdict: LineVerdict | None = None
    serves_only: bool | None = None


@dataclass(frozen=True, eq=False)
class ShotMapSummary:
    count: int
    verdict_counts: Mapping[LineVerdict, int]
    depth_expected_counts: Mapping[DepthZone, float]
    width_counts: Mapping[WidthZone, int]
    median_sigma_along_m: float | None
    landings: tuple[ShotLanding, ...]


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def depth_zone_probabilities(y_from_net_m: float, sigma_along_m: float) -> Mapping[DepthZone, float]:
    """P(zone) for a bounce ``y_from_net_m`` from the net (in its own half)
    with along-court sigma ``sigma_along_m``. Sums to 1. A zero sigma puts all
    mass on the point-estimate zone (edges belong to the zone nearer the net).

    Raises:
        ValueError: on a negative or non-finite sigma.
    """
    if not math.isfinite(sigma_along_m) or sigma_along_m < 0.0:
        raise ValueError(f"sigma must be a finite non-negative number, got {sigma_along_m}")
    distance = abs(y_from_net_m)
    if sigma_along_m == 0.0:
        zone = depth_zone_at(distance)
        return MappingProxyType({z: 1.0 if z is zone else 0.0 for z in _DEPTH_ORDER})
    below = [_normal_cdf((edge - distance) / sigma_along_m) for edge in DEPTH_ZONE_EDGES_M]
    values = [below[0], below[1] - below[0], below[2] - below[1], 1.0 - below[2]]
    return MappingProxyType({z: max(v, 0.0) for z, v in zip(_DEPTH_ORDER, values, strict=True)})


def _sigma_along(bounce: BouncePoint) -> float:
    return math.sqrt(max(float(bounce.cov_m2[1, 1]), 0.0))


def classify_landing(
    bounce: BouncePoint,
    call: LineCall | None,
    hitter_end: CourtEnd | None,
    shot_type: str | None,
    is_serve: bool,
    *,
    target: LineCallTarget,
    hit_xy_m: tuple[float, float] | None = None,
) -> ShotLanding:
    """Zones for one landing. Width is in thirds of the singles width in the
    camera's x frame."""
    x, y = bounce.court_xy_m
    distance = abs(y)
    sigma = _sigma_along(bounce)
    zone = depth_zone_at(distance)
    reach = DEPTH_CONFIDENCE_Z * sigma
    low, high = max(distance - reach, 0.0), distance + reach
    confident = depth_zone_at(low) is zone and depth_zone_at(high) is zone
    return ShotLanding(
        bounce=bounce,
        call=call,
        hitter_end=hitter_end,
        shot_type=shot_type,
        is_serve=is_serve,
        target=target,
        depth_zone=zone,
        depth_confident=confident,
        depth_probabilities=depth_zone_probabilities(distance, sigma),
        width_zone=width_zone_at(x),
        hit_xy_m=hit_xy_m,
    )


def filter_landings(landings: Sequence[ShotLanding], flt: ShotMapFilter) -> tuple[ShotLanding, ...]:
    """Landings matching every set field of ``flt``. A verdict filter drops
    landings that carry no call."""
    kept: list[ShotLanding] = []
    for landing in landings:
        if flt.hitter_end is not None and landing.hitter_end is not flt.hitter_end:
            continue
        if flt.shot_type is not None and landing.shot_type != flt.shot_type:
            continue
        if flt.verdict is not None and (landing.call is None or landing.call.verdict is not flt.verdict):
            continue
        if flt.serves_only is not None and landing.is_serve is not flt.serves_only:
            continue
        kept.append(landing)
    return tuple(kept)


def summarize_shot_map(landings: Sequence[ShotLanding]) -> ShotMapSummary:
    """Counts per verdict (landings without a call count toward none),
    expected depth counts (sums of probabilities, so they add up to
    ``count``), width counts, and the median along-court sigma (None when
    empty)."""
    verdicts = {verdict: 0 for verdict in LineVerdict}
    depth = {zone: 0.0 for zone in _DEPTH_ORDER}
    width = {zone: 0 for zone in WidthZone}
    for landing in landings:
        if landing.call is not None:
            verdicts[landing.call.verdict] += 1
        for zone, probability in landing.depth_probabilities.items():
            depth[zone] += probability
        width[landing.width_zone] += 1
    sigmas = [_sigma_along(landing.bounce) for landing in landings]
    return ShotMapSummary(
        count=len(landings),
        verdict_counts=MappingProxyType(verdicts),
        depth_expected_counts=MappingProxyType(depth),
        width_counts=MappingProxyType(width),
        median_sigma_along_m=statistics.median(sigmas) if sigmas else None,
        landings=tuple(landings),
    )
