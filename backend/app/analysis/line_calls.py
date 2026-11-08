"""Bounce to court metres, its uncertainty, and the three-valued call (6.3).

PURE: numpy and stdlib. Only BOUNCE events may pass through the ground
homography (5.1): ``bounce_to_court`` refuses anything else.

Ground covariance (1 sigma, m^2) of a bounce:

* image: the event's fit covariance (break point and time term, 5.2) plus
  the camera's rms shake squared on each axis (brief Constants: always added),
  pushed through the image -> court Jacobian;
* calibration: tap error propagated to that point (``calibration_covariance``);
* both scaled by the parallax correction's factor (5.1), then by
  ``SIGMA_INFLATION`` squared (1.0 until section 11 coverage sets it).

Calls (6.3), per edge of the target region with outward normal n:
``margin = signed distance inside + BALL_CONTACT_PATCH_RADIUS_M`` (a ball
touching a line is good), ``r = z sqrt(n^T Sigma n)``. OUT if any edge has
margin <= -r, IN if every edge has margin >= +r, else TOO_CLOSE_TO_CALL. The
deciding line is the edge with the least |margin| / r.

Decisions taken here:

* The net is a region edge but not a court line (``court_model``), so it is
  never a deciding line and is not judged: which side of the net a bounce is
  on is not a line call. The caller calls a bounce only against a region in
  the bounce's own half.
* "one_sided_bounce on the deciding direction" forces TOO_CLOSE when the
  deciding line runs ACROSS the court (a baseline or service line): a missing
  side widens the time term along the ball's path, which runs mostly along
  the court.
* G21 lives in ``call_bounce``: no call at all (None, "not measured") unless
  the calibration allows calls. ``call_region`` is the geometry alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.analysis.ball_events import ONE_SIDED_BOUNCE_FLAG, WEAK_BOUNCE_CONFIDENCE, WEAK_BOUNCE_FLAG, BallEvent
from app.analysis.camera_stability import CameraStability
from app.analysis.court_calibration import CourtCalibration
from app.analysis.court_model import CourtEnd, CourtLine, CourtRegion, half_at, keypoint_xy
from app.analysis.homography import (
    BALL_RADIUS_M,
    Float64Array,
    apply_homography,
    calibration_covariance,
    correct_ball_centre_parallax,
    image_to_court_jacobian,
)
from app.models.court import BallEventKind, LineCallTarget, LineVerdict

__all__ = [
    "LINE_CALLS_VERSION",
    "BouncePoint",
    "LineCall",
    "LineVerdict",
    "bounce_to_court",
    "call_bounce",
    "call_region",
    "uncertainty_ellipse",
]

LINE_CALLS_VERSION: Final[str] = "line_calls_v1"
#: 6.3: the ball's contact patch counts toward IN [estimate, verify].
BALL_CONTACT_PATCH_RADIUS_M: Final[float] = 0.02
LINE_CALL_Z: Final[float] = 1.96
#: FROZEN at 1.0 on 2026-11-08 from section 11 coverage. Band coverage (true
#: margin within +/- r) was 0.996 at T0 (255 calls, 100 seeds) and 1.0 at T2
#: 1080p (7 calls): ABOVE the 0.92-0.98 target, so the covariance is, if
#: anything, conservative. Inflation only widens it (design: > 1.5 means a
#: covariance bug), and narrowing on synthetic data alone would raise the
#: risk of confident wrong calls on real footage. Revisit with owner footage.
SIGMA_INFLATION: Final[float] = 1.0

_EDGE_NORMALS: Final[dict[str, tuple[float, float]]] = {
    "x_min": (-1.0, 0.0),
    "x_max": (1.0, 0.0),
    "y_min": (0.0, -1.0),
    "y_max": (0.0, 1.0),
}
_ACROSS_COURT_LINES: Final[frozenset[CourtLine]] = frozenset(
    {
        CourtLine.NEAR_BASELINE,
        CourtLine.FAR_BASELINE,
        CourtLine.NEAR_SERVICE_LINE,
        CourtLine.FAR_SERVICE_LINE,
    }
)


@dataclass(frozen=True, eq=False)
class BouncePoint:
    event_id: str
    time_s: float
    #: Brief G8.
    time_sigma_s: float
    court_xy_m: tuple[float, float]
    #: 1 sigma, m^2.
    cov_m2: Float64Array
    half: CourtEnd
    confidence: float
    flags: tuple[str, ...]


@dataclass(frozen=True)
class LineCall:
    verdict: LineVerdict
    deciding_line: CourtLine
    signed_margin_m: float
    normal_sigma_m: float
    uncertainty_radius_m: float
    flags: tuple[str, ...]
    version: str
    #: Brief G8: the region this was called against.
    target: LineCallTarget


def bounce_to_court(
    event: BallEvent, calibration: CourtCalibration, stability: CameraStability
) -> BouncePoint:
    """A BOUNCE event's ground contact point and 1-sigma covariance.

    Raises:
        ValueError: for any event that is not a BOUNCE; an in-flight point
            has no ground position.
    """
    if event.kind is not BallEventKind.BOUNCE:
        raise ValueError(f"only bounces map to the ground, not {event.kind.value}")
    uv = event.image_xy_px
    mapped = apply_homography(calibration.h_image_to_court, np.array([uv], dtype=np.float64))[0]
    pose = calibration.pose
    court = correct_ball_centre_parallax((float(mapped[0]), float(mapped[1])), pose)
    shake_px = stability.rms_shift_px or 0.0
    image_cov = np.asarray(event.image_cov_px2, dtype=np.float64) + np.eye(2) * shake_px**2
    jacobian = image_to_court_jacobian(calibration.h_image_to_court, uv)
    taps_court = np.array([keypoint_xy(t.keypoint) for t in calibration.taps], dtype=np.float64)
    taps_image = np.array([(t.u_px, t.v_px) for t in calibration.taps], dtype=np.float64)
    tap_cov = calibration_covariance(taps_court, taps_image, uv, calibration.sigma_tap_px)
    parallax = (pose.position_m[2] - BALL_RADIUS_M) / pose.position_m[2]
    cov = (jacobian @ image_cov @ jacobian.T + tap_cov) * parallax**2 * SIGMA_INFLATION**2
    cov = 0.5 * (cov + cov.T)
    return BouncePoint(
        event_id=event.event_id,
        time_s=event.time_s,
        time_sigma_s=event.time_sigma_s,
        court_xy_m=court,
        cov_m2=np.asarray(cov, dtype=np.float64),
        half=half_at(court[1]),
        confidence=event.confidence,
        flags=event.flags,
    )


def uncertainty_ellipse(cov_m2: Float64Array, z: float = LINE_CALL_Z) -> tuple[float, float, float]:
    """(major, minor, angle_deg) semi-axes at ``z`` sigma. The angle is the
    major axis from court +x, counter-clockwise, in [0, 180) (brief G16)."""
    cov = np.asarray(cov_m2, dtype=np.float64)
    values, vectors = np.linalg.eigh(0.5 * (cov + cov.T))
    values = np.clip(values, 0.0, None)
    major_vector = vectors[:, 1]
    angle = math.degrees(math.atan2(float(major_vector[1]), float(major_vector[0]))) % 180.0
    if angle >= 180.0:
        angle = 0.0
    return z * math.sqrt(float(values[1])), z * math.sqrt(float(values[0])), angle


def _inside_distance(edge: str, region: CourtRegion, xy: tuple[float, float]) -> float:
    x, y = xy
    return {
        "x_min": x - region.x_min,
        "x_max": region.x_max - x,
        "y_min": y - region.y_min,
        "y_max": region.y_max - y,
    }[edge]


def call_region(
    bounce: BouncePoint,
    region: CourtRegion,
    *,
    target: LineCallTarget,
    z: float = LINE_CALL_Z,
) -> LineCall:
    """The three-valued call of ``bounce`` against ``region``'s line edges."""
    judged: list[tuple[float, float, float, str]] = []  # (|m| / r, margin, sigma, edge)
    for edge in sorted(region.edge_lines):
        normal = np.asarray(_EDGE_NORMALS[edge])
        margin = _inside_distance(edge, region, bounce.court_xy_m) + BALL_CONTACT_PATCH_RADIUS_M
        sigma = math.sqrt(max(float(normal @ bounce.cov_m2 @ normal), 0.0))
        radius = z * sigma
        closeness = abs(margin) / radius if radius > 0.0 else (0.0 if margin == 0.0 else math.inf)
        judged.append((closeness, margin, sigma, edge))
    if not judged:
        raise ValueError(f"region {region.name} has no line edges")
    if any(margin <= -z * sigma for _, margin, sigma, _ in judged):
        verdict = LineVerdict.OUT
    elif all(margin >= z * sigma for _, margin, sigma, _ in judged):
        verdict = LineVerdict.IN
    else:
        verdict = LineVerdict.TOO_CLOSE_TO_CALL
    if verdict is LineVerdict.OUT:
        # The line the ball is clearly beyond decides an OUT.
        deciding = min((j for j in judged if j[1] <= -z * j[2]), key=lambda j: (j[1], j[3]))
    else:
        deciding = min(judged, key=lambda j: (j[0], j[3]))
    _, margin, sigma, edge = deciding
    line = region.edge_lines[edge]
    flags: list[str] = []
    if bounce.confidence < WEAK_BOUNCE_CONFIDENCE or WEAK_BOUNCE_FLAG in bounce.flags:
        flags.append(WEAK_BOUNCE_FLAG)
    if ONE_SIDED_BOUNCE_FLAG in bounce.flags and line in _ACROSS_COURT_LINES:
        flags.append(ONE_SIDED_BOUNCE_FLAG)
    if flags:
        verdict = LineVerdict.TOO_CLOSE_TO_CALL
    return LineCall(
        verdict=verdict,
        deciding_line=line,
        signed_margin_m=float(margin),
        normal_sigma_m=float(sigma),
        uncertainty_radius_m=float(z * sigma),
        flags=tuple(flags),
        version=LINE_CALLS_VERSION,
        target=target,
    )


def call_bounce(
    bounce: BouncePoint,
    region: CourtRegion,
    calibration: CourtCalibration,
    *,
    target: LineCallTarget,
    z: float = LINE_CALL_Z,
) -> LineCall | None:
    """``call_region``, or None ("not measured") when the calibration does
    not allow calls (brief G21: fewer than 8 accepted taps)."""
    if not calibration.calls_allowed:
        return None
    return call_region(bounce, region, target=target, z=z)
