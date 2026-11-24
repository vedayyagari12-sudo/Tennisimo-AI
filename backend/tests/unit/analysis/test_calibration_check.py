"""``app/analysis/calibration_check.py``: the rating a player is shown before
a rally is wasted."""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.calibration_check import (
    BASELINE_CUT_OFF,
    CALLS_OFF,
    FEW_LINES,
    GOOD_SUPPORT,
    POOR_SUPPORT,
    TAP_ERROR,
    WEAK_MATCH,
    check_calibration,
    corners_in_frame,
    describe,
)


def auto(**overrides: object):  # type: ignore[no-untyped-def]
    args = dict(source="auto", corners_inside=4, calls_allowed=True, support=0.95, lines_visible=10)
    args.update(overrides)
    return check_calibration(**args)  # type: ignore[arg-type]


def test_a_strong_complete_fit_is_good_with_no_reasons() -> None:
    check = auto()
    assert check.confidence == "good" and check.reasons == () and check.calls_allowed


def test_a_weaker_match_asks_the_player_to_check() -> None:
    check = auto(support=(GOOD_SUPPORT + POOR_SUPPORT) / 2)
    assert check.confidence == "check" and check.reasons == (WEAK_MATCH,)


def test_a_barely_accepted_fit_is_poor() -> None:
    check = auto(support=POOR_SUPPORT - 0.01)
    assert check.confidence == "poor" and WEAK_MATCH in check.reasons


def test_few_court_lines_lower_the_rating() -> None:
    assert auto(lines_visible=8).confidence == "check"
    assert auto(lines_visible=8).reasons == (FEW_LINES,)
    assert auto(lines_visible=5).confidence == "poor"


def test_a_court_cut_off_by_the_picture_is_flagged() -> None:
    one_corner_out = auto(corners_inside=3)
    assert one_corner_out.confidence == "check" and one_corner_out.reasons == (BASELINE_CUT_OFF,)
    assert auto(corners_inside=2).confidence == "poor"
    assert auto(corners_inside=0).confidence == "poor"


def test_calls_being_off_is_a_reason_even_for_a_strong_fit() -> None:
    check = auto(calls_allowed=False)
    assert check.confidence == "check" and check.reasons == (CALLS_OFF,)


def test_reasons_are_listed_once_and_in_the_order_found() -> None:
    check = auto(corners_inside=3, support=0.80, lines_visible=8, calls_allowed=False)
    assert check.reasons == (BASELINE_CUT_OFF, WEAK_MATCH, FEW_LINES, CALLS_OFF)
    assert len(set(check.reasons)) == len(check.reasons)
    assert len(describe(check.reasons)) == 4


def test_tapped_points_are_rated_on_their_own_agreement() -> None:
    good = check_calibration(source="taps", corners_inside=4, calls_allowed=True, tap_rms_px=1.2)
    assert good.confidence == "good" and good.support is None and good.lines_visible is None
    bad = check_calibration(source="taps", corners_inside=4, calls_allowed=True, tap_rms_px=3.5)
    assert bad.confidence == "check" and bad.reasons == (TAP_ERROR,)


def test_corners_are_counted_through_the_homography() -> None:
    # A homography that maps court metres to a 1920x1080 picture, centred.
    scale = 60.0
    h = np.array([[scale, 0.0, 960.0], [0.0, -scale * 0.5, 540.0], [0.0, 0.0, 1.0]])
    assert corners_in_frame(h, (1920, 1080)) == 4
    shifted = h.copy()
    shifted[0, 2] = 1700.0  # the court pushed right: the right-hand corners leave the picture
    assert corners_in_frame(shifted, (1920, 1080)) == 2
    at_infinity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    assert corners_in_frame(at_infinity, (1920, 1080)) == 0


@pytest.mark.parametrize("support", [0.72, 0.8, 0.9, 1.0])
def test_the_rating_never_gets_better_when_the_evidence_gets_worse(support: float) -> None:
    order = {"good": 0, "check": 1, "poor": 2}
    better = auto(support=min(support + 0.1, 1.0), lines_visible=10)
    worse = auto(support=support, lines_visible=10)
    assert order[better.confidence] <= order[worse.confidence]
