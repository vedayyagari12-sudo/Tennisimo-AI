"""ITF court geometry: dimensions, keypoints, lines, regions and zone edges.

PURE: numbers in, numbers out. No numpy needed.

Coordinates (SESSION_MODE_PHASE2.md 3.1): origin at court centre on the
ground; +x is right seen from the near baseline looking at the far baseline;
+y runs toward the far baseline; z is up. "Near" is the end nearer the camera.
"Left" and "right" are always in this camera frame, at both ends.

Every dimension runs to the OUTER edge of a line (lines are part of the court),
so every keypoint is an outer-edge intersection (brief G19). The one exception
is the centre service line, which is centred on x = 0 and belongs to both
service boxes: each box's inner edge sits at the FAR edge of that line,
x = -/+ CENTRE_SERVICE_LINE_WIDTH_M / 2 (brief G12).

The net (y = 0) is a region edge but not a court line, so it never appears in
``CourtRegion.edge_lines``. Deciding what a bounce on the wrong side of the net
means is ``line_calls``' job, not this module's.

Enums are defined once in ``app.models.court`` and re-exported here (brief G18).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from app.models.court import (
    CourtEnd,
    CourtFormat,
    CourtKeypoint,
    CourtLine,
    DepthZone,
    WidthZone,
)

__all__ = [
    "COURT_MODEL_VERSION",
    "CourtEnd",
    "CourtFormat",
    "CourtKeypoint",
    "CourtLine",
    "CourtRegion",
    "DepthZone",
    "WidthZone",
    "court_line_endpoints",
    "depth_zone_at",
    "half_at",
    "half_region",
    "keypoint_xy",
    "service_box_region",
    "width_zone_at",
]

COURT_MODEL_VERSION: Final[str] = "court_itf_v1"

# ITF dimensions in metres [verify against current Rules of Tennis].
COURT_LENGTH_M: Final[float] = 23.77
HALF_LENGTH_M: Final[float] = 11.885
SINGLES_WIDTH_M: Final[float] = 8.23
DOUBLES_WIDTH_M: Final[float] = 10.97
SINGLES_HALF_WIDTH_M: Final[float] = 4.115
DOUBLES_HALF_WIDTH_M: Final[float] = 5.485
SERVICE_LINE_FROM_NET_M: Final[float] = 6.40
CENTRE_SERVICE_LINE_WIDTH_M: Final[float] = 0.05
#: Net height at the centre strap and at the posts.
NET_HEIGHT_CENTRE_M: Final[float] = 0.914
NET_HEIGHT_POST_M: Final[float] = 1.07
#: Posts stand 0.914 m outside the doubles sidelines.
NET_POST_HALF_SPAN_M: Final[float] = DOUBLES_HALF_WIDTH_M + 0.914

#: Depth zone edges, metres from the net in the bounce's half (design 6.2):
#: short | 6.40 | mid | 9.1425 | deep | 11.885 | beyond.
MID_DEEP_EDGE_FROM_NET_M: Final[float] = (SERVICE_LINE_FROM_NET_M + HALF_LENGTH_M) / 2.0
DEPTH_ZONE_EDGES_M: Final[tuple[float, float, float]] = (
    SERVICE_LINE_FROM_NET_M,
    MID_DEEP_EDGE_FROM_NET_M,
    HALF_LENGTH_M,
)
#: Width zones: singles width in thirds, in the camera's x frame.
WIDTH_ZONE_THIRD_M: Final[float] = SINGLES_WIDTH_M / 3.0
#: Serve zones (wide, body, t): the 4.115 m box in thirds.
SERVE_ZONE_THIRD_M: Final[float] = SINGLES_HALF_WIDTH_M / 3.0

_EDGE_X_MIN: Final[str] = "x_min"
_EDGE_X_MAX: Final[str] = "x_max"
_EDGE_Y_MIN: Final[str] = "y_min"
_EDGE_Y_MAX: Final[str] = "y_max"

_SX: Final[float] = SINGLES_HALF_WIDTH_M
_DX: Final[float] = DOUBLES_HALF_WIDTH_M
_BY: Final[float] = HALF_LENGTH_M
_SY: Final[float] = SERVICE_LINE_FROM_NET_M

_KEYPOINTS: Final[Mapping[CourtKeypoint, tuple[float, float]]] = MappingProxyType(
    {
        CourtKeypoint.NEAR_LEFT_DOUBLES_CORNER: (-_DX, -_BY),
        CourtKeypoint.NEAR_RIGHT_DOUBLES_CORNER: (_DX, -_BY),
        CourtKeypoint.FAR_LEFT_DOUBLES_CORNER: (-_DX, _BY),
        CourtKeypoint.FAR_RIGHT_DOUBLES_CORNER: (_DX, _BY),
        CourtKeypoint.NEAR_LEFT_SINGLES_CORNER: (-_SX, -_BY),
        CourtKeypoint.NEAR_RIGHT_SINGLES_CORNER: (_SX, -_BY),
        CourtKeypoint.FAR_LEFT_SINGLES_CORNER: (-_SX, _BY),
        CourtKeypoint.FAR_RIGHT_SINGLES_CORNER: (_SX, _BY),
        CourtKeypoint.NEAR_LEFT_SERVICE_CORNER: (-_SX, -_SY),
        CourtKeypoint.NEAR_RIGHT_SERVICE_CORNER: (_SX, -_SY),
        CourtKeypoint.FAR_LEFT_SERVICE_CORNER: (-_SX, _SY),
        CourtKeypoint.FAR_RIGHT_SERVICE_CORNER: (_SX, _SY),
        CourtKeypoint.NEAR_SERVICE_T: (0.0, -_SY),
        CourtKeypoint.FAR_SERVICE_T: (0.0, _SY),
    }
)

_LINES: Final[Mapping[CourtLine, tuple[tuple[float, float], tuple[float, float]]]] = (
    MappingProxyType(
        {
            CourtLine.NEAR_BASELINE: ((-_DX, -_BY), (_DX, -_BY)),
            CourtLine.FAR_BASELINE: ((-_DX, _BY), (_DX, _BY)),
            CourtLine.NEAR_SERVICE_LINE: ((-_SX, -_SY), (_SX, -_SY)),
            CourtLine.FAR_SERVICE_LINE: ((-_SX, _SY), (_SX, _SY)),
            CourtLine.LEFT_SINGLES_SIDELINE: ((-_SX, -_BY), (-_SX, _BY)),
            CourtLine.RIGHT_SINGLES_SIDELINE: ((_SX, -_BY), (_SX, _BY)),
            CourtLine.LEFT_DOUBLES_SIDELINE: ((-_DX, -_BY), (-_DX, _BY)),
            CourtLine.RIGHT_DOUBLES_SIDELINE: ((_DX, -_BY), (_DX, _BY)),
            CourtLine.CENTRE_SERVICE_LINE: ((0.0, -_SY), (0.0, _SY)),
        }
    )
)


@dataclass(frozen=True)
class CourtRegion:
    """Axis-aligned target region. ``edge_lines`` maps ``x_min`` / ``x_max`` /
    ``y_min`` / ``y_max`` to the court line forming that edge; the net edge is
    absent because the net is not a court line."""

    name: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    edge_lines: Mapping[str, CourtLine]


def keypoint_xy(keypoint: CourtKeypoint) -> tuple[float, float]:
    """Court metres of a tappable keypoint."""
    return _KEYPOINTS[keypoint]


def court_line_endpoints(line: CourtLine) -> tuple[tuple[float, float], tuple[float, float]]:
    """Endpoints of a line along its outer edge, court metres.

    Baselines run the doubles width; service lines and the centre service line
    run between the singles sidelines and the net-side ends of the boxes.
    """
    return _LINES[line]


def half_at(y_m: float) -> CourtEnd:
    """The half containing ``y_m``. A point exactly under the net counts as far."""
    return CourtEnd.NEAR if y_m < 0.0 else CourtEnd.FAR


def half_region(end: CourtEnd, fmt: CourtFormat) -> CourtRegion:
    """The full singles or doubles half at ``end``, out to its baseline."""
    singles = fmt is CourtFormat.SINGLES
    half_width = _SX if singles else _DX
    left = CourtLine.LEFT_SINGLES_SIDELINE if singles else CourtLine.LEFT_DOUBLES_SIDELINE
    right = CourtLine.RIGHT_SINGLES_SIDELINE if singles else CourtLine.RIGHT_DOUBLES_SIDELINE
    if end is CourtEnd.NEAR:
        return CourtRegion(
            name=f"{fmt.value}_half_near",
            x_min=-half_width,
            x_max=half_width,
            y_min=-_BY,
            y_max=0.0,
            edge_lines=MappingProxyType(
                {_EDGE_X_MIN: left, _EDGE_X_MAX: right, _EDGE_Y_MIN: CourtLine.NEAR_BASELINE}
            ),
        )
    return CourtRegion(
        name=f"{fmt.value}_half_far",
        x_min=-half_width,
        x_max=half_width,
        y_min=0.0,
        y_max=_BY,
        edge_lines=MappingProxyType(
            {_EDGE_X_MIN: left, _EDGE_X_MAX: right, _EDGE_Y_MAX: CourtLine.FAR_BASELINE}
        ),
    )


def service_box_region(end: CourtEnd, side: Literal["deuce", "ad"]) -> CourtRegion:
    """The service box at ``end`` (the receiver's end), on ``side``.

    Deuce is the RECEIVER's right (brief G12). The near receiver faces +y, so
    near deuce is x > 0; the far receiver faces -y, so far deuce is x < 0. Each
    box includes the centre service line: its inner edge is the far edge of
    that line, x = -/+ 0.025.
    """
    inner = CENTRE_SERVICE_LINE_WIDTH_M / 2.0
    positive_x = (end is CourtEnd.NEAR) == (side == "deuce")
    if positive_x:
        x_min, x_max = -inner, _SX
        x_edges = {_EDGE_X_MIN: CourtLine.CENTRE_SERVICE_LINE, _EDGE_X_MAX: CourtLine.RIGHT_SINGLES_SIDELINE}
    else:
        x_min, x_max = -_SX, inner
        x_edges = {_EDGE_X_MIN: CourtLine.LEFT_SINGLES_SIDELINE, _EDGE_X_MAX: CourtLine.CENTRE_SERVICE_LINE}
    if end is CourtEnd.NEAR:
        y_min, y_max = -_SY, 0.0
        y_edges = {_EDGE_Y_MIN: CourtLine.NEAR_SERVICE_LINE}
    else:
        y_min, y_max = 0.0, _SY
        y_edges = {_EDGE_Y_MAX: CourtLine.FAR_SERVICE_LINE}
    return CourtRegion(
        name=f"service_box_{side}_{end.value}",
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        edge_lines=MappingProxyType({**x_edges, **y_edges}),
    )


def depth_zone_at(distance_from_net_m: float) -> DepthZone:
    """Point-estimate depth zone. Each edge belongs to the zone nearer the net,
    because a ball on the line is in: 6.40 is ``short``, 11.885 is ``deep``."""
    distance = abs(distance_from_net_m)
    if distance <= DEPTH_ZONE_EDGES_M[0]:
        return DepthZone.SHORT
    if distance <= DEPTH_ZONE_EDGES_M[1]:
        return DepthZone.MID
    if distance <= DEPTH_ZONE_EDGES_M[2]:
        return DepthZone.DEEP
    return DepthZone.BEYOND


def width_zone_at(x_m: float) -> WidthZone:
    """Point-estimate width zone in thirds of the singles width, camera x frame.
    The two inner edges belong to ``centre``."""
    if x_m < -WIDTH_ZONE_THIRD_M / 2.0:
        return WidthZone.LEFT
    if x_m > WIDTH_ZONE_THIRD_M / 2.0:
        return WidthZone.RIGHT
    return WidthZone.CENTRE
