"""Build step 2: ITF numbers, keypoint symmetry, box regions (G12), zones, G19 lines."""

from __future__ import annotations

import math

import pytest

from app.analysis import court_model as cm
from app.analysis.court_model import (
    CourtEnd,
    CourtFormat,
    CourtKeypoint,
    CourtLine,
    DepthZone,
    WidthZone,
    court_line_endpoints,
    depth_zone_at,
    half_at,
    half_region,
    keypoint_xy,
    service_box_region,
    width_zone_at,
)


def test_itf_numbers_are_exact() -> None:
    assert cm.COURT_LENGTH_M == 23.77
    assert cm.HALF_LENGTH_M * 2.0 == pytest.approx(cm.COURT_LENGTH_M, abs=1e-12)
    assert cm.SINGLES_WIDTH_M == 8.23
    assert cm.DOUBLES_WIDTH_M == 10.97
    assert cm.SINGLES_HALF_WIDTH_M * 2.0 == pytest.approx(cm.SINGLES_WIDTH_M, abs=1e-12)
    assert cm.DOUBLES_HALF_WIDTH_M * 2.0 == pytest.approx(cm.DOUBLES_WIDTH_M, abs=1e-12)
    assert cm.SERVICE_LINE_FROM_NET_M == 6.40
    assert cm.CENTRE_SERVICE_LINE_WIDTH_M == 0.05
    assert cm.COURT_MODEL_VERSION == "court_itf_v1"


def test_fourteen_keypoints_are_symmetric() -> None:
    points = {k: keypoint_xy(k) for k in CourtKeypoint}
    assert len(points) == 14
    by_xy = {xy: k for k, xy in points.items()}
    assert len(by_xy) == 14, "keypoints must be distinct"
    for xy in points.values():
        assert (-xy[0], xy[1]) in by_xy, "left-right mirror missing"
        assert (xy[0], -xy[1]) in by_xy, "near-far mirror missing"


def test_keypoints_sit_on_outer_edges_and_t_on_centre() -> None:
    assert keypoint_xy(CourtKeypoint.NEAR_LEFT_SINGLES_CORNER) == (-4.115, -11.885)
    assert keypoint_xy(CourtKeypoint.FAR_RIGHT_DOUBLES_CORNER) == (5.485, 11.885)
    assert keypoint_xy(CourtKeypoint.NEAR_RIGHT_SERVICE_CORNER) == (4.115, -6.40)
    assert keypoint_xy(CourtKeypoint.NEAR_SERVICE_T) == (0.0, -6.40)
    assert keypoint_xy(CourtKeypoint.FAR_SERVICE_T) == (0.0, 6.40)


@pytest.mark.parametrize("keypoint", list(CourtKeypoint))
def test_names_encode_end_and_side(keypoint: CourtKeypoint) -> None:
    x, y = keypoint_xy(keypoint)
    assert (y < 0) == keypoint.value.startswith("near_")
    if "_left_" in keypoint.value:
        assert x < 0
    elif "_right_" in keypoint.value:
        assert x > 0
    else:
        assert x == 0.0


def test_every_keypoint_lies_on_two_court_lines() -> None:
    def on_line(xy: tuple[float, float], line: CourtLine) -> bool:
        (x1, y1), (x2, y2) = court_line_endpoints(line)
        cross = (x2 - x1) * (xy[1] - y1) - (y2 - y1) * (xy[0] - x1)
        within = min(x1, x2) - 1e-9 <= xy[0] <= max(x1, x2) + 1e-9 and min(
            y1, y2
        ) - 1e-9 <= xy[1] <= max(y1, y2) + 1e-9
        return abs(cross) < 1e-9 and within

    for keypoint in CourtKeypoint:
        lines = [line for line in CourtLine if on_line(keypoint_xy(keypoint), line)]
        assert len(lines) >= 2, f"{keypoint} is not an intersection: {lines}"


def test_g19_line_endpoints() -> None:
    assert court_line_endpoints(CourtLine.NEAR_BASELINE) == ((-5.485, -11.885), (5.485, -11.885))
    assert court_line_endpoints(CourtLine.FAR_SERVICE_LINE) == ((-4.115, 6.40), (4.115, 6.40))
    assert court_line_endpoints(CourtLine.CENTRE_SERVICE_LINE) == ((0.0, -6.40), (0.0, 6.40))
    assert court_line_endpoints(CourtLine.RIGHT_DOUBLES_SIDELINE) == (
        (5.485, -11.885),
        (5.485, 11.885),
    )
    assert len({court_line_endpoints(line) for line in CourtLine}) == 9


def test_half_at() -> None:
    assert half_at(-0.01) is CourtEnd.NEAR
    assert half_at(-11.0) is CourtEnd.NEAR
    assert half_at(0.0) is CourtEnd.FAR
    assert half_at(5.0) is CourtEnd.FAR


def test_half_regions() -> None:
    near_singles = half_region(CourtEnd.NEAR, CourtFormat.SINGLES)
    assert (near_singles.x_min, near_singles.x_max) == (-4.115, 4.115)
    assert (near_singles.y_min, near_singles.y_max) == (-11.885, 0.0)
    assert near_singles.edge_lines == {
        "x_min": CourtLine.LEFT_SINGLES_SIDELINE,
        "x_max": CourtLine.RIGHT_SINGLES_SIDELINE,
        "y_min": CourtLine.NEAR_BASELINE,
    }
    far_doubles = half_region(CourtEnd.FAR, CourtFormat.DOUBLES)
    assert (far_doubles.x_min, far_doubles.x_max) == (-5.485, 5.485)
    assert (far_doubles.y_min, far_doubles.y_max) == (0.0, 11.885)
    assert far_doubles.edge_lines["y_max"] is CourtLine.FAR_BASELINE
    assert "y_min" not in far_doubles.edge_lines, "the net is not a court line"


def test_all_four_service_boxes_g12() -> None:
    near_deuce = service_box_region(CourtEnd.NEAR, "deuce")
    assert (near_deuce.x_min, near_deuce.x_max) == (-0.025, 4.115)
    assert (near_deuce.y_min, near_deuce.y_max) == (-6.40, 0.0)
    assert near_deuce.edge_lines == {
        "x_min": CourtLine.CENTRE_SERVICE_LINE,
        "x_max": CourtLine.RIGHT_SINGLES_SIDELINE,
        "y_min": CourtLine.NEAR_SERVICE_LINE,
    }

    near_ad = service_box_region(CourtEnd.NEAR, "ad")
    assert (near_ad.x_min, near_ad.x_max) == (-4.115, 0.025)
    assert (near_ad.y_min, near_ad.y_max) == (-6.40, 0.0)
    assert near_ad.edge_lines["x_max"] is CourtLine.CENTRE_SERVICE_LINE
    assert near_ad.edge_lines["x_min"] is CourtLine.LEFT_SINGLES_SIDELINE

    far_deuce = service_box_region(CourtEnd.FAR, "deuce")
    assert (far_deuce.x_min, far_deuce.x_max) == (-4.115, 0.025)
    assert (far_deuce.y_min, far_deuce.y_max) == (0.0, 6.40)
    assert far_deuce.edge_lines == {
        "x_min": CourtLine.LEFT_SINGLES_SIDELINE,
        "x_max": CourtLine.CENTRE_SERVICE_LINE,
        "y_max": CourtLine.FAR_SERVICE_LINE,
    }

    far_ad = service_box_region(CourtEnd.FAR, "ad")
    assert (far_ad.x_min, far_ad.x_max) == (-0.025, 4.115)
    assert (far_ad.y_min, far_ad.y_max) == (0.0, 6.40)

    # Both boxes at one end overlap on exactly the centre line's width.
    assert near_ad.x_max - near_deuce.x_min == pytest.approx(0.05, abs=1e-12)
    assert len({r.name for r in (near_deuce, near_ad, far_deuce, far_ad)}) == 4


def test_depth_zone_edges() -> None:
    assert cm.DEPTH_ZONE_EDGES_M == (6.40, 9.1425, 11.885)
    assert depth_zone_at(0.0) is DepthZone.SHORT
    assert depth_zone_at(6.40) is DepthZone.SHORT
    assert depth_zone_at(math.nextafter(6.40, 99.0)) is DepthZone.MID
    assert depth_zone_at(9.1425) is DepthZone.MID
    assert depth_zone_at(math.nextafter(9.1425, 99.0)) is DepthZone.DEEP
    assert depth_zone_at(11.885) is DepthZone.DEEP
    assert depth_zone_at(math.nextafter(11.885, 99.0)) is DepthZone.BEYOND
    # Distance is unsigned: the near half mirrors the far half.
    assert depth_zone_at(-10.0) is DepthZone.DEEP


def test_width_and_serve_zone_edges() -> None:
    third = 8.23 / 3.0
    assert cm.WIDTH_ZONE_THIRD_M == pytest.approx(third, abs=1e-12)
    assert cm.SERVE_ZONE_THIRD_M == pytest.approx(4.115 / 3.0, abs=1e-12)
    assert width_zone_at(0.0) is WidthZone.CENTRE
    assert width_zone_at(third / 2.0) is WidthZone.CENTRE
    assert width_zone_at(-third / 2.0) is WidthZone.CENTRE
    assert width_zone_at(math.nextafter(third / 2.0, 9.0)) is WidthZone.RIGHT
    assert width_zone_at(math.nextafter(-third / 2.0, -9.0)) is WidthZone.LEFT
    assert width_zone_at(1.82) is WidthZone.RIGHT  # the section 7.3 sample landing
