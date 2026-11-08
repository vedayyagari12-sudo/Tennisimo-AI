"""Build step 12: ``analysis/placement.py`` (design 6.2).

Done when: probabilities sum to 1; zones exact at boundaries; filters by
side, shot, verdict, serve; soft counts sum to count; too-close not added to
in.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analysis.court_model import CourtEnd, CourtLine, DepthZone, WidthZone
from app.analysis.line_calls import LINE_CALLS_VERSION, BouncePoint, LineCall
from app.analysis.placement import (
    ShotLanding,
    ShotMapFilter,
    classify_landing,
    depth_zone_probabilities,
    filter_landings,
    summarize_shot_map,
)
from app.models.court import LineCallTarget, LineVerdict

TARGET = LineCallTarget.SINGLES_HALF


def bounce(x: float, y: float, sigma_along: float = 0.1, sigma_across: float = 0.05) -> BouncePoint:
    return BouncePoint("b_000001", 1.0, 0.004, (x, y), np.diag([sigma_across**2, sigma_along**2]),
                       CourtEnd.NEAR if y < 0 else CourtEnd.FAR, 0.9, ())


def call(verdict: LineVerdict) -> LineCall:
    return LineCall(verdict, CourtLine.FAR_BASELINE, 0.3, 0.1, 0.196, (), LINE_CALLS_VERSION, TARGET)


def landing(y: float, verdict: LineVerdict | None = LineVerdict.IN, *, x: float = 0.0, sigma: float = 0.1,
            hitter: CourtEnd | None = CourtEnd.NEAR, shot: str | None = None, serve: bool = False) -> ShotLanding:
    return classify_landing(bounce(x, y, sigma), None if verdict is None else call(verdict), hitter, shot, serve,
                            target=TARGET)


# ------------------------------------------------------------ probabilities


def test_probabilities_sum_to_one_over_a_sweep() -> None:
    rng = np.random.default_rng(12)
    for _ in range(500):
        probabilities = depth_zone_probabilities(rng.uniform(-14.0, 14.0), rng.uniform(0.0, 2.0))
        assert set(probabilities) == set(DepthZone)
        assert math.fsum(probabilities.values()) == pytest.approx(1.0, abs=1e-12)
        assert all(0.0 <= p <= 1.0 for p in probabilities.values())


def test_probabilities_follow_the_normal_cdf() -> None:
    at_edge = depth_zone_probabilities(6.40, 0.3)
    assert at_edge[DepthZone.SHORT] == pytest.approx(0.5)
    assert at_edge[DepthZone.MID] == pytest.approx(0.5, abs=1e-6)
    deep = depth_zone_probabilities(10.5, 0.5)
    assert deep[DepthZone.DEEP] == pytest.approx(
        0.5 * (math.erf(1.385 / 0.5 / math.sqrt(2)) + math.erf(1.3575 / 0.5 / math.sqrt(2))), abs=1e-9
    )
    with pytest.raises(ValueError):
        depth_zone_probabilities(5.0, -0.1)


@pytest.mark.parametrize(
    ("distance", "zone"),
    [(0.0, DepthZone.SHORT), (6.40, DepthZone.SHORT), (6.41, DepthZone.MID), (9.1425, DepthZone.MID),
     (9.15, DepthZone.DEEP), (11.885, DepthZone.DEEP), (11.9, DepthZone.BEYOND)],
)
def test_zones_exact_at_boundaries(distance: float, zone: DepthZone) -> None:
    exact = depth_zone_probabilities(distance, 0.0)
    assert exact[zone] == 1.0 and math.fsum(exact.values()) == 1.0
    for y in (distance, -distance):
        assert landing(y, sigma=0.0).depth_zone is zone


def test_width_zones_in_thirds_of_the_singles_width() -> None:
    third = 8.23 / 3.0
    assert landing(5.0, x=-third / 2.0 - 0.01).width_zone is WidthZone.LEFT
    assert landing(5.0, x=-third / 2.0).width_zone is WidthZone.CENTRE
    assert landing(5.0, x=third / 2.0).width_zone is WidthZone.CENTRE
    assert landing(5.0, x=third / 2.0 + 0.01).width_zone is WidthZone.RIGHT


def test_depth_confident_only_when_the_95_range_stays_in_one_zone() -> None:
    assert landing(8.0, sigma=0.3).depth_confident
    assert not landing(8.0, sigma=0.9).depth_confident  # 8 +/- 1.76 crosses 6.40 and 9.1425
    assert not landing(11.7, sigma=0.15).depth_confident
    assert landing(3.0, sigma=1.0).depth_confident  # short, and below 0 is still short


# ------------------------------------------------------------------- filters


def test_filters_by_side_shot_verdict_and_serve() -> None:
    landings = [
        landing(5.0, LineVerdict.IN, hitter=CourtEnd.NEAR, shot="forehand"),
        landing(12.5, LineVerdict.OUT, hitter=CourtEnd.NEAR, shot="backhand"),
        landing(-8.0, LineVerdict.TOO_CLOSE_TO_CALL, hitter=CourtEnd.FAR, shot="forehand"),
        landing(4.0, None, hitter=None, shot=None, serve=True),
        landing(3.0, LineVerdict.IN, hitter=CourtEnd.NEAR, shot="serve", serve=True),
    ]
    assert filter_landings(landings, ShotMapFilter()) == tuple(landings)
    assert filter_landings(landings, ShotMapFilter(hitter_end=CourtEnd.NEAR)) == (landings[0], landings[1], landings[4])
    assert filter_landings(landings, ShotMapFilter(shot_type="forehand")) == (landings[0], landings[2])
    assert filter_landings(landings, ShotMapFilter(verdict=LineVerdict.IN)) == (landings[0], landings[4])
    assert filter_landings(landings, ShotMapFilter(serves_only=True)) == (landings[3], landings[4])
    assert filter_landings(landings, ShotMapFilter(serves_only=False)) == tuple(landings[:3])
    both = ShotMapFilter(hitter_end=CourtEnd.NEAR, verdict=LineVerdict.IN, serves_only=False)
    assert filter_landings(landings, both) == (landings[0],)


# ------------------------------------------------------------------ summary


def test_soft_counts_sum_to_count_and_too_close_is_not_in() -> None:
    landings = [
        landing(6.3, LineVerdict.IN, sigma=0.5),
        landing(9.1, LineVerdict.IN, sigma=0.8),
        landing(11.85, LineVerdict.TOO_CLOSE_TO_CALL, sigma=0.4),
        landing(11.88, LineVerdict.TOO_CLOSE_TO_CALL, sigma=0.4),
        landing(13.0, LineVerdict.OUT, sigma=0.3, x=3.0),
        landing(2.0, None, x=-3.0),
    ]
    summary = summarize_shot_map(landings)
    assert summary.count == 6 and summary.landings == tuple(landings)
    assert math.fsum(summary.depth_expected_counts.values()) == pytest.approx(6.0)
    assert dict(summary.verdict_counts) == {LineVerdict.IN: 2, LineVerdict.OUT: 1, LineVerdict.TOO_CLOSE_TO_CALL: 2}
    assert sum(summary.verdict_counts.values()) == 5  # the uncalled landing counts toward none
    assert dict(summary.width_counts) == {WidthZone.LEFT: 1, WidthZone.CENTRE: 4, WidthZone.RIGHT: 1}
    assert summary.median_sigma_along_m == pytest.approx(0.4)
    deep_whole = sum(1 for item in landings if item.depth_zone is DepthZone.DEEP)
    assert summary.depth_expected_counts[DepthZone.DEEP] < deep_whole


def test_empty_summary() -> None:
    summary = summarize_shot_map([])
    assert summary.count == 0 and summary.median_sigma_along_m is None
    assert all(v == 0 for v in summary.verdict_counts.values())
    assert all(v == 0.0 for v in summary.depth_expected_counts.values())
