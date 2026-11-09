"""Bounce, hit, net and unknown kinks on ball flights (design 5.2-5.4).

PURE: numpy and stdlib only. Image pixels are TRACK_SPACE, y DOWN, so an
upward image impulse has J_row < 0.

A ground homography is valid only for points ON the ground (5.1). No event
here carries a court position: ``line_calls.bounce_to_court`` maps BOUNCE
events only. Classification may use the ground map of a break point solely as
a gate ("within court + run-off" for a bounce, whose point IS on the ground)
and the camera scale at that row.

Pipeline (``detect_ball_events``): break points per flight
(``find_break_points``), rule classification (``classify_event``), pose hits
at flight starts, the grammar pass (``resolve_event_grammar``), hitter ends,
then final ids in time order (brief G9): ``{b|h|n|k}_{index:06d}``.

Decisions taken here (the design and brief do not cover them):

* ``BallEvent`` gains ``end: CourtEnd | None``. A HIT's end is the hitter's;
  rally and score logic need it, and the 6.5 dataclass had nowhere to put it.
  Pose hits give it directly. Without pose it is the half of the PREVIOUS
  bounce (the ball bounced on the hitter's side), else the opposite of the
  NEXT bounce's half, else None. "Side from position" (5.4 rule 4) is not
  used: an in-flight point has no valid ground position (a near serve at
  2.7 m maps to the FAR half from the recommended placement).
* "Along-court direction reverses" (5.4 rules 2-4) is read from the
  HORIZONTAL image velocity. The image row cannot carry it: at the far
  baseline a falling ball moving away still moves DOWN the image (the court
  is foreshortened about 8x there), and up after the bounce.
* A pose hit within ``POSE_HIT_WINDOW_S`` of a flight's first sample makes a
  HIT event there (the serve or feed): the ball appearing at a tracked
  player's contact is evidence, not invention. Its ``velocity_before`` is NaN.
* The single-segment fit is the better of a quadratic and a projected
  parabola (denominator constant fixed at 1, so it cannot degenerate as the
  homogeneous ``flight_track`` fit can on short windows), so a near-camera flight's perspective curve is
  not mistaken for a kink. A break also needs that fit's RMS to be at least
  ``BREAK_MIN_SINGLE_RMS_PX``.
* Rules 2 and 3 judge speed in 3D, not by the image speed ratio rho: a
  bounce's point is on the ground and a net contact's is on the net plane,
  so each has a known 3D position and image Jacobian, and a small linear
  program checks the velocities (``bounce_is_consistent``,
  ``net_contact_is_consistent``). Far-court foreshortening inverts rho:
  falling while receding nearly cancels in the image, so a far bounce and a
  net strike both GAIN image speed. Rule 4 keeps rho for hits.
* The grammar pass is a first-order Viterbi over labels {bounce, hit} for
  events whose confidence is under ``GRAMMAR_RELABEL_MAX_CONFIDENCE``; others
  are fixed. It never adds or removes an event.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np
from scipy.optimize import linprog

from app.analysis.court_calibration import CourtCalibration
from app.analysis.court_model import (
    DOUBLES_HALF_WIDTH_M,
    HALF_LENGTH_M,
    NET_HEIGHT_CENTRE_M,
    NET_HEIGHT_POST_M,
    NET_POST_HALF_SPAN_M,
    CourtEnd,
    half_at,
)
from app.analysis.flight_track import PROJECTED_FIT_MIN_POINTS, BallFlight
from app.ball.track import V_MAX_M_PER_S
from app.analysis.homography import (
    Float64Array,
    apply_homography,
    image_scale_px_per_m,
    project_points_3d,
)
from app.models.court import BallEventKind

__all__ = [
    "BALL_EVENTS_VERSION",
    "BallEvent",
    "BallEventKind",
    "PoseHit",
    "classify_event",
    "hitter_ground_point",
    "merge_split_bounces",
    "detect_ball_events",
    "find_break_points",
    "resolve_event_grammar",
]

BALL_EVENTS_VERSION: Final[str] = "ball_events_v1"

#: 5.2: up to 8 samples within 0.15 s on each side of a candidate break.
SIDE_MAX_SAMPLES: Final[int] = 8
SIDE_MAX_SPAN_S: Final[float] = 0.15
#: A side needs 2 samples (a velocity); fewer than 3 on a side is one-sided.
SIDE_MIN_SAMPLES: Final[int] = 2
SIDE_FULL_SAMPLES: Final[int] = 3
#: Sides this short are fitted with a line, not a quadratic.
SIDE_LINE_MAX_SAMPLES: Final[int] = 3
#: The single fit needs residual degrees of freedom to judge anything.
BREAK_MIN_TOTAL_SAMPLES: Final[int] = 6
#: Brief Constants: keep a break when rss2 <= 0.5 x rss1.
BREAK_MIN_RSS_RATIO: Final[float] = 0.5
#: The single fit must miss by at least this RMS for a break to mean anything.
BREAK_MIN_SINGLE_RMS_PX: Final[float] = 1.0
#: Two breaks need at least this many samples between them (each piece of a
#: segment keeps a velocity).
BREAK_MIN_SEPARATION_SAMPLES: Final[int] = 3
#: Floor on the fit noise used for covariance (estimate; brief budget 1.2 px).
FIT_SIGMA_FLOOR_PX: Final[float] = 0.5
#: Event confidence = clip(1 - rss2/rss1) x min(1, min(n_before, n_after)/8).
CONFIDENCE_FULL_SIDE: Final[int] = 8
_BREAK_TIME_GRID: Final[int] = 41

#: 5.4 rule 1.
POSE_HIT_WINDOW_S: Final[float] = 0.05
POSE_HIT_WRIST_GATE_M: Final[float] = 1.2
#: 5.4: J_min = 2 m/s x f / range, in px/s.
J_MIN_M_S: Final[float] = 2.0
#: 5.4 rule 2.
NET_POLYGON_MARGIN_PX: Final[float] = 2.0
#: Net contact (replaces 5.4's image ratio rho < 0.5): the ball keeps at most
#: half its along-court speed, having had at least a rally ball's.
NET_ALONG_RETENTION_MAX: Final[float] = 0.5
NET_MIN_ALONG_M_S: Final[float] = 5.0
#: 5.4 rule 3.
RUNOFF_M: Final[float] = 4.0
#: Bounce consistency (replaces 5.4's image speed ratio for rule 3): the
#: horizontal retention grid and the most a bounce may return vertically.
BOUNCE_RETENTION_MIN: Final[float] = 0.3
BOUNCE_RETENTION_MAX: Final[float] = 1.0
BOUNCE_RETENTION_STEPS: Final[int] = 15
#: Vertical restitution is 0.73-0.76 in the ITF rebound test; 0.9 is margin.
BOUNCE_RESTITUTION_MAX: Final[float] = 0.9
#: A real bounce reverses a vertical speed of at least this much, m/s.
BOUNCE_MIN_VERTICAL_M_S: Final[float] = 0.5
#: ... and of at most this much: a 10 m lob lands at about 14 m/s.
BOUNCE_MAX_VERTICAL_M_S: Final[float] = 15.0
#: Ground speed at a bounce, per axis (a 100 mph serve lands near 31 m/s).
BOUNCE_MAX_GROUND_M_S: Final[float] = 50.0
#: Velocity tolerance: this fraction of the side's image speed plus an
#: absolute floor at the local scale. 25 % let a near hit at 1 m pass as a
#: bounce; 15 % classifies T0 bounces exactly as 25 % did (58 of 59).
BOUNCE_VELOCITY_TOL_FRACTION: Final[float] = 0.15
BOUNCE_VELOCITY_TOL_M_S: Final[float] = 0.5
#: 5.4 rule 4.
HIT_SPEED_RATIO_MIN: Final[float] = 1.2
#: Horizontal image speed below which its direction is not judged, m/s.
DIRECTION_MIN_SPEED_M_S: Final[float] = 1.0

#: Brief Constants.
GRAMMAR_RELABEL_MAX_CONFIDENCE: Final[float] = 0.6
WEAK_BOUNCE_CONFIDENCE: Final[float] = 0.5
#: Viterbi costs: a relabel costs its confidence; these transitions cost more
#: than the plain hit -> bounce -> hit alternation.
GRAMMAR_HIT_AFTER_HIT_COST: Final[float] = 0.5
GRAMMAR_BOUNCE_AFTER_BOUNCE_COST: Final[float] = 0.3

ONE_SIDED_BOUNCE_FLAG: Final[str] = "one_sided_bounce"
WEAK_BOUNCE_FLAG: Final[str] = "weak_bounce"
POSE_HIT_FLAG: Final[str] = "pose_hit"
FLIGHT_START_FLAG: Final[str] = "flight_start"
GRAMMAR_RELABELLED_FLAG: Final[str] = "grammar_relabelled"
SPLIT_BOUNCE_FLAG: Final[str] = "split_bounce_merged"
#: A bounce whose break was found twice, a few frames apart: the early break
#: takes the downward side and reads as a hit (speed up, no reversal), the
#: late one has no downward side and stays a kink. T2 seed 20270001: a far
#: bounce split 3 frames apart added a hit that never happened.
SPLIT_BOUNCE_MAX_GAP_S: Final[float] = 0.1

_ID_PREFIX: Final[dict[BallEventKind, str]] = {
    BallEventKind.BOUNCE: "b",
    BallEventKind.HIT: "h",
    BallEventKind.NET_CONTACT: "n",
    BallEventKind.UNKNOWN_KINK: "k",
}


@dataclass(frozen=True)
class PoseHit:
    """A contact from the pose pipeline. ``shot_type`` (brief G10) is the only
    producer for ``is_serve`` in practice mode and for landing shot types."""

    time_s: float
    wrist_xy_px: tuple[float, float] | None
    end: CourtEnd | None
    confidence: float
    shot_type: str | None = None
    #: Midpoint of the hitter's ankles in the image. Feet are on the ground,
    #: so this is the one pose point the court homography may map to metres.
    feet_xy_px: tuple[float, float] | None = None


@dataclass(frozen=True, eq=False)
class BallEvent:
    event_id: str
    flight_id: int
    kind: BallEventKind
    time_s: float
    time_sigma_s: float
    image_xy_px: tuple[float, float]
    image_cov_px2: Float64Array
    velocity_before_px_s: tuple[float, float]
    velocity_after_px_s: tuple[float, float]
    confidence: float
    flags: tuple[str, ...]
    #: The hitter's end for a HIT; None otherwise or when unknown.
    end: CourtEnd | None = None
    #: The matched pose hit's shot type, for HITs made from pose.
    shot_type: str | None = None
    #: The matched pose hit's feet in the image, for HITs made from pose.
    #: Pixels only: court metres are made from it in ``court_session``.
    hitter_feet_xy_px: tuple[float, float] | None = None


# ----------------------------------------------------------------- fitting


@dataclass(frozen=True, eq=False)
class _SideFit:
    coefficients: Float64Array  # (degree + 1, 2), highest power first, origin t0
    inverse_normal: Float64Array  # (X^T X)^-1
    rss: float
    count: int
    degree: int


def _fit_side(times: Float64Array, xy: Float64Array, origin: float) -> _SideFit:
    """A line through 2-3 samples, a quadratic from 4: a side with 3 samples
    keeps a residual degree of freedom, as ``flight_track``'s pieces do, or
    any short side would fit exactly and every window would look broken."""
    degree = 1 if len(times) <= SIDE_LINE_MAX_SAMPLES else 2
    basis = np.vander(times - origin, degree + 1)
    coefficients, *_ = np.linalg.lstsq(basis, xy, rcond=None)
    residual = xy - basis @ coefficients
    return _SideFit(
        coefficients=np.asarray(coefficients, dtype=np.float64),
        inverse_normal=np.linalg.pinv(basis.T @ basis),
        rss=float(np.sum(residual**2)),
        count=len(times),
        degree=degree,
    )


def _evaluate(fit: _SideFit, t: float) -> Float64Array:
    powers = np.array([t ** (fit.degree - k) for k in range(fit.degree + 1)])
    return np.asarray(powers @ fit.coefficients, dtype=np.float64)


def _derivative(fit: _SideFit, t: float) -> Float64Array:
    powers = np.array(
        [(fit.degree - k) * t ** (fit.degree - k - 1) if fit.degree - k > 0 else 0.0 for k in range(fit.degree + 1)]
    )
    return np.asarray(powers @ fit.coefficients, dtype=np.float64)


def _position_variance(fit: _SideFit, t: float, sigma2: float) -> float:
    powers = np.array([t ** (fit.degree - k) for k in range(fit.degree + 1)])
    return float(sigma2 * powers @ fit.inverse_normal @ powers)


def _projected_rss(times: Float64Array, xy: Float64Array) -> float:
    """RSS of the best pinhole image of a 3D parabola, u = A(t)/C(t),
    v = B(t)/C(t), with C(t) = 1 + c1 t + c2 t^2 (its constant fixed, so the
    solve is ordinary least squares and cannot collapse to a degenerate
    solution). ``inf`` when C vanishes or changes sign in the window."""
    span = times - times.mean()
    t = span / max(float(np.max(np.abs(span))), 1e-9)
    centre = xy.mean(axis=0)
    size = max(float(np.max(np.abs(xy - centre))), 1.0)
    p = (xy - centre) / size
    ones, zeros = np.ones_like(t), np.zeros_like(t)
    rows_u = np.column_stack([ones, t, t * t, zeros, zeros, zeros, -p[:, 0] * t, -p[:, 0] * t * t])
    rows_v = np.column_stack([zeros, zeros, zeros, ones, t, t * t, -p[:, 1] * t, -p[:, 1] * t * t])
    solution, *_ = np.linalg.lstsq(np.vstack([rows_u, rows_v]), np.concatenate([p[:, 0], p[:, 1]]), rcond=None)
    denominator = 1.0 + solution[6] * t + solution[7] * t * t
    if np.any(denominator <= 1e-6):
        return math.inf
    basis = np.column_stack([ones, t, t * t])
    fitted = np.column_stack([basis @ solution[:3], basis @ solution[3:6]]) / denominator[:, None]
    return float(np.sum(((fitted - p) * size) ** 2))


def _single_rss(times: Float64Array, xy: Float64Array) -> float:
    """The better of a quadratic and the projected parabola (6+ samples)."""
    basis = np.vander(times - times[0], 3)
    coefficients, *_ = np.linalg.lstsq(basis, xy, rcond=None)
    rss = float(np.sum((xy - basis @ coefficients) ** 2))
    if len(times) >= PROJECTED_FIT_MIN_POINTS:
        rss = min(rss, _projected_rss(times, xy))
    return rss


def _break_time(before: _SideFit, after: _SideFit, low: float, high: float, middle: float) -> float:
    """Where the two row curves cross inside [low, high] (closest to
    ``middle``); else where the two paths come closest."""
    size = max(before.degree, after.degree) + 1
    row_b = np.zeros(size)
    row_a = np.zeros(size)
    row_b[size - before.degree - 1 :] = before.coefficients[:, 1]
    row_a[size - after.degree - 1 :] = after.coefficients[:, 1]
    difference = np.trim_zeros(row_b - row_a, "f")
    roots: list[float] = []
    if difference.size > 1:
        for root in np.roots(difference):
            if abs(root.imag) < 1e-9 and low <= root.real <= high:
                roots.append(float(root.real))
    if roots:
        return min(roots, key=lambda r: (abs(r - middle), r))
    grid = np.linspace(low, high, _BREAK_TIME_GRID)
    gaps = [float(np.sum((_evaluate(before, t) - _evaluate(after, t)) ** 2)) for t in grid]
    return float(grid[int(np.argmin(gaps))])


def _median_interval(times: Float64Array) -> float:
    deltas = np.diff(times)
    deltas = deltas[deltas > 0.0]
    return float(np.median(deltas)) if deltas.size else 0.0


def _sides(
    times: Float64Array, split: int, low: int = 0, high: int | None = None
) -> tuple[list[int], list[int]]:
    """Up to 8 samples within 0.15 s each side of the split, inside the
    segment [low, high] (bounded by stronger breaks already chosen)."""
    last = len(times) - 1 if high is None else high
    before = [j for j in range(split, low - 1, -1) if times[split] - times[j] <= SIDE_MAX_SPAN_S + 1e-9][
        :SIDE_MAX_SAMPLES
    ][::-1]
    after = [j for j in range(split + 1, last + 1) if times[j] - times[split + 1] <= SIDE_MAX_SPAN_S + 1e-9][
        :SIDE_MAX_SAMPLES
    ]
    return before, after


def _split_fit(
    times: Float64Array, xy: Float64Array, split: int, low: int = 0, high: int | None = None
) -> tuple[_SideFit, _SideFit, float, float] | None:
    """(before fit, after fit, rss2 / rss1, origin) for a split between
    detections ``split`` and ``split + 1``; None when it cannot be judged."""
    before, after = _sides(times, split, low, high)
    if (
        min(len(before), len(after)) < SIDE_MIN_SAMPLES
        or max(len(before), len(after)) < SIDE_FULL_SAMPLES
        or len(before) + len(after) < BREAK_MIN_TOTAL_SAMPLES
    ):
        return None
    both = np.array(before + after)
    origin = float(times[split])
    fit_b = _fit_side(times[before], xy[before], origin)
    fit_a = _fit_side(times[after], xy[after], origin)
    rss1 = _single_rss(times[both], xy[both])
    if rss1 <= 0.0 or math.sqrt(rss1 / len(both)) < BREAK_MIN_SINGLE_RMS_PX:
        return None
    return fit_b, fit_a, (fit_b.rss + fit_a.rss) / rss1, origin


def _refine_split(
    times: Float64Array, xy: Float64Array, split: int, low: int = 0, high: int | None = None
) -> int:
    """The split with the least two-piece RSS over the fixed window of
    ``split``'s two sides (each piece keeps at least 2 samples)."""
    before, after = _sides(times, split, low, high)
    window = before + after
    best = (math.inf, split)
    for cut in range(SIDE_MIN_SAMPLES, len(window) - SIDE_MIN_SAMPLES + 1):
        left, right = window[:cut], window[cut:]
        origin = float(times[left[-1]])
        rss = _fit_side(times[left], xy[left], origin).rss + _fit_side(times[right], xy[right], origin).rss
        best = min(best, (rss, left[-1]))
    return best[1]


def find_break_points(flight: BallFlight) -> tuple[BallEvent, ...]:
    """Kinks in one flight (5.2), as UNKNOWN_KINK events with temporary ids
    ``tmp_{flight}_{n}`` (brief G9), in time order. Coasted samples are never
    used."""
    found = np.flatnonzero(flight.detected)
    times = flight.times_s[found]
    xy = flight.xy_px[found]
    interval = _median_interval(flight.times_s)
    candidates: list[tuple[float, float, int]] = []
    fits: dict[int, tuple[_SideFit, _SideFit, float, float]] = {}
    for split in range(len(times) - 1):
        fit = _split_fit(times, xy, split)
        if fit is not None and fit[2] <= BREAK_MIN_RSS_RATIO:
            candidates.append((fit[2], float(times[split]), split))
            fits[split] = fit

    # Segment, strongest first. A candidate is re-judged with its sides cut
    # at the breaks already chosen: an echo of a stronger kink then sees a
    # clean piece and fails, while a real second kink (a net drop 0.1 s
    # before its bounce) keeps its own. Each survivor is refined on ONE
    # window: the ratio's denominator changes with the window, so comparing
    # ratios across splits can pick a split a few frames off.
    chosen: list[int] = []
    for _, _, split in sorted(candidates):
        if any(abs(split - other) < BREAK_MIN_SEPARATION_SAMPLES for other in chosen):
            continue
        low = max((c + 1 for c in chosen if c < split), default=0)
        high = min((c for c in chosen if c > split), default=None)
        fit = _split_fit(times, xy, split, low, high)
        if fit is None or fit[2] > BREAK_MIN_RSS_RATIO:
            continue
        best = _refine_split(times, xy, split, low, high)
        if best != split:
            refined = _split_fit(times, xy, best, low, high)
            if refined is not None and refined[2] <= BREAK_MIN_RSS_RATIO:
                split, fit = best, refined
        if any(abs(split - other) < BREAK_MIN_SEPARATION_SAMPLES for other in chosen):
            continue
        chosen.append(split)
        fits[split] = fit

    events: list[BallEvent] = []
    for number, split in enumerate(sorted(chosen)):
        fit_b, fit_a, ratio, origin = fits[split]
        t_left, t_right = float(times[split]), float(times[split + 1])
        local = _break_time(
            fit_b,
            fit_a,
            t_left - interval - origin,
            t_right + interval - origin,
            0.5 * (t_left + t_right) - origin,
        )
        p_b, p_a = _evaluate(fit_b, local), _evaluate(fit_a, local)
        v_b, v_a = _derivative(fit_b, local), _derivative(fit_a, local)
        params = 2 * ((fit_b.degree + 1) + (fit_a.degree + 1))
        dof = 2 * (fit_b.count + fit_a.count) - params
        sigma2 = max((fit_b.rss + fit_a.rss) / dof if dof > 0 else 0.0, FIT_SIGMA_FLOOR_PX**2)
        var_b = _position_variance(fit_b, local, sigma2)
        var_a = _position_variance(fit_a, local, sigma2)
        one_sided = min(fit_b.count, fit_a.count) < SIDE_FULL_SAMPLES
        frame_bound = (interval if interval > 0.0 else t_right - t_left) / math.sqrt(12.0)
        if one_sided:
            sigma_t = frame_bound
        else:
            closing = abs(float(v_a[1] - v_b[1]))
            fitted = math.sqrt(var_b + var_a) / closing if closing > 0.0 else math.inf
            sigma_t = min(fitted, (t_right - t_left) / math.sqrt(12.0))
        mean_velocity = 0.5 * (v_b + v_a)
        cov = np.eye(2) * 0.25 * (var_b + var_a) + np.outer(mean_velocity, mean_velocity) * sigma_t**2
        confidence = float(np.clip(1.0 - ratio, 0.0, 1.0)) * min(
            1.0, min(fit_b.count, fit_a.count) / CONFIDENCE_FULL_SIDE
        )
        point = 0.5 * (p_b + p_a)
        events.append(
            BallEvent(
                event_id=f"tmp_{flight.flight_id}_{number}",
                flight_id=flight.flight_id,
                kind=BallEventKind.UNKNOWN_KINK,
                time_s=origin + local,
                time_sigma_s=float(sigma_t),
                image_xy_px=(float(point[0]), float(point[1])),
                image_cov_px2=np.asarray(cov, dtype=np.float64),
                velocity_before_px_s=(float(v_b[0]), float(v_b[1])),
                velocity_after_px_s=(float(v_a[0]), float(v_a[1])),
                confidence=confidence,
                flags=(ONE_SIDED_BOUNCE_FLAG,) if one_sided else (),
            )
        )
    return tuple(events)


# ---------------------------------------------------------- classification


def _ground_point(calibration: CourtCalibration, uv: tuple[float, float]) -> tuple[float, float] | None:
    """Ground map of an image point, or None above the horizon. Valid as a
    POSITION only for a point on the ground."""
    h = calibration.h_image_to_court
    w = float(h[2] @ np.array([uv[0], uv[1], 1.0]))
    reference = float(h[2] @ np.append(apply_homography(calibration.h_court_to_image, np.zeros((1, 2)))[0], 1.0))
    if w == 0.0 or (w > 0.0) != (reference > 0.0):
        return None
    xy = apply_homography(h, np.array([uv], dtype=np.float64))[0]
    return (float(xy[0]), float(xy[1]))


def _scale_at(calibration: CourtCalibration, uv: tuple[float, float]) -> float:
    """Pixels per metre at the ground point under ``uv``; above the horizon,
    the scale at the far baseline centre (the smallest on court)."""
    if _ground_point(calibration, uv) is not None:
        return image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, uv)
    far = apply_homography(calibration.h_court_to_image, np.array([[0.0, HALF_LENGTH_M]]))[0]
    return image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, (float(far[0]), float(far[1])))


def net_polygon_px(calibration: CourtCalibration) -> Float64Array:
    """The net as an image polygon: post bases, post tops, centre strap top."""
    xyz = np.array(
        [
            [-NET_POST_HALF_SPAN_M, 0.0, 0.0],
            [NET_POST_HALF_SPAN_M, 0.0, 0.0],
            [NET_POST_HALF_SPAN_M, 0.0, NET_HEIGHT_POST_M],
            [0.0, 0.0, NET_HEIGHT_CENTRE_M],
            [-NET_POST_HALF_SPAN_M, 0.0, NET_HEIGHT_POST_M],
        ]
    )
    return project_points_3d(calibration.pose, xyz)


def _inside_polygon(point: tuple[float, float], polygon: Float64Array, margin_px: float) -> bool:
    """Even-odd test, or within ``margin_px`` of an edge."""
    x, y = point
    inside = False
    count = len(polygon)
    for k in range(count):
        (x1, y1), (x2, y2) = polygon[k], polygon[(k + 1) % count]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        s = 0.0 if length2 == 0.0 else min(max(((x - x1) * dx + (y - y1) * dy) / length2, 0.0), 1.0)
        if math.hypot(x - (x1 + s * dx), y - (y1 + s * dy)) <= margin_px:
            return True
    return inside


def _speed_ratio(event: BallEvent) -> float:
    before = math.hypot(*event.velocity_before_px_s)
    after = math.hypot(*event.velocity_after_px_s)
    return after / before if before > 0.0 else math.inf


def _horizontal_reverses(event: BallEvent, scale: float) -> bool:
    """Horizontal image direction flips, both sides moving clearly."""
    floor = DIRECTION_MIN_SPEED_M_S * scale
    u_before, u_after = event.velocity_before_px_s[0], event.velocity_after_px_s[0]
    return abs(u_before) >= floor and abs(u_after) >= floor and (u_before > 0.0) != (u_after > 0.0)


def _image_jacobian_3d(
    calibration: CourtCalibration, ground: tuple[float, float], z_m: float = 0.0
) -> Float64Array:
    """2x3 d(u, v) / d(x, y, z) at a court point, from the 3x4 projection."""
    m = calibration.pose.projection_matrix
    point = np.array([ground[0], ground[1], z_m, 1.0])
    w = float(m[2] @ point)
    uv = (m[:2] @ point) / w
    return np.asarray((m[:2, :3] - np.outer(uv, m[2, :3])) / w, dtype=np.float64)


def bounce_is_consistent(
    event: BallEvent, calibration: CourtCalibration, ground: tuple[float, float]
) -> bool:
    """Could these image velocities be one ground bounce at ``ground``?

    The break point of a bounce IS on the ground, so its 3D position is
    known and the image Jacobian J = d(u, v)/d(x, y, z) there is exact. For a
    horizontal retention r on a grid in [BOUNCE_RETENTION_MIN,
    BOUNCE_RETENTION_MAX], a linear program asks for a ground velocity g and
    vertical speeds with ``J (g, vz-) ~ v-`` and ``J (r g, vz+) ~ v+`` within
    a tolerance, falling (vz- < 0), then rising no faster (0 < vz+ <=
    BOUNCE_RESTITUTION_MAX |vz-|), at bounce speeds, travelling away from the
    net. The last is what separates a near-side hit without pose: it can be
    explained as a fast bounce only by a ball receding toward the net. An exact solve is
    ill-conditioned (the horizontal rows hardly see along-court speed), hence
    the tolerance. This replaces the 5.4 image speed ratio, which far-court
    foreshortening inverts: a far bounce GAINS image speed as fall-and-recede
    becomes rise-and-recede.
    """
    jacobian = _image_jacobian_3d(calibration, ground)
    j_ground, dz = jacobian[:, :2], jacobian[:, 2]
    before = np.asarray(event.velocity_before_px_s, dtype=np.float64)
    after = np.asarray(event.velocity_after_px_s, dtype=np.float64)
    if not (np.all(np.isfinite(before)) and np.all(np.isfinite(after))):
        return False
    scale = image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, event.image_xy_px)
    tolerance = np.concatenate(
        [
            np.full(2, BOUNCE_VELOCITY_TOL_FRACTION * float(np.hypot(*before))),
            np.full(2, BOUNCE_VELOCITY_TOL_FRACTION * float(np.hypot(*after))),
        ]
    ) + BOUNCE_VELOCITY_TOL_M_S * scale
    measured = np.concatenate([before, after])
    # Unknowns: gx, gy, vz-, vz+ (m/s).
    inequality = np.array([[0.0, 0.0, 1.0, 0.0], [0.0, 0.0, -1.0, -1.0], [0.0, 0.0, BOUNCE_RESTITUTION_MAX, 1.0]])
    inequality_rhs = np.array([-BOUNCE_MIN_VERTICAL_M_S, -BOUNCE_MIN_VERTICAL_M_S, 0.0])
    inequality[1] = [0.0, 0.0, 0.0, -1.0]
    # Along-court speed points away from the net, into the bounce's half: a
    # ball bouncing on the near half was hit from the far end.
    along = (0.0, BOUNCE_MAX_GROUND_M_S) if ground[1] >= 0.0 else (-BOUNCE_MAX_GROUND_M_S, 0.0)
    bounds = [(-BOUNCE_MAX_GROUND_M_S, BOUNCE_MAX_GROUND_M_S), along] + [
        (-BOUNCE_MAX_VERTICAL_M_S, 0.0),
        (0.0, BOUNCE_MAX_VERTICAL_M_S),
    ]
    for retention in np.linspace(BOUNCE_RETENTION_MIN, BOUNCE_RETENTION_MAX, BOUNCE_RETENTION_STEPS):
        model = np.zeros((4, 4))
        model[:2, :2] = j_ground
        model[:2, 2] = dz
        model[2:, :2] = retention * j_ground
        model[2:, 3] = dz
        a_ub = np.vstack([model, -model, inequality])
        b_ub = np.concatenate([measured + tolerance, tolerance - measured, inequality_rhs])
        result = linprog(np.zeros(4), A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if result.status == 0:
            return True
    return False


def net_plane_point(calibration: CourtCalibration, uv: tuple[float, float]) -> tuple[float, float, float] | None:
    """Where the camera ray through ``uv`` crosses the net plane y = 0, if it
    does so in front of the camera within the net (posts to ground to top)."""
    m = calibration.pose.projection_matrix
    centre = np.asarray(calibration.pose.position_m, dtype=np.float64)
    direction = np.linalg.solve(m[:, :3], np.array([uv[0], uv[1], 1.0]))
    if direction[1] == 0.0:
        return None
    reach = -centre[1] / direction[1]
    if reach <= 0.0:
        return None
    x, _, z = centre + reach * direction
    if abs(x) > NET_POST_HALF_SPAN_M or not 0.0 <= z <= NET_HEIGHT_POST_M:
        return None
    return (float(x), 0.0, float(z))


def net_contact_is_consistent(event: BallEvent, calibration: CourtCalibration) -> bool:
    """Could this be the ball striking the net? On the net plane the 3D point
    is known; a linear program asks for velocities that reproduce the image
    velocities (same tolerance as a bounce) with an along-court speed of at
    least ``NET_MIN_ALONG_M_S`` before and at most ``NET_ALONG_RETENTION_MAX``
    of it after, either way along the court. This replaces 5.4 rule 2's image
    speed ratio, which a ball falling as it recedes inverts."""
    point = net_plane_point(calibration, event.image_xy_px)
    if point is None:
        return False
    before = np.asarray(event.velocity_before_px_s, dtype=np.float64)
    after = np.asarray(event.velocity_after_px_s, dtype=np.float64)
    if not (np.all(np.isfinite(before)) and np.all(np.isfinite(after))):
        return False
    jacobian = _image_jacobian_3d(calibration, (point[0], point[1]), point[2])
    scale = calibration.pose.focal_px / math.dist(calibration.pose.position_m, point)
    tolerance = np.concatenate(
        [
            np.full(2, BOUNCE_VELOCITY_TOL_FRACTION * float(np.hypot(*before))),
            np.full(2, BOUNCE_VELOCITY_TOL_FRACTION * float(np.hypot(*after))),
        ]
    ) + BOUNCE_VELOCITY_TOL_M_S * scale
    measured = np.concatenate([before, after])
    model = np.zeros((4, 6))  # unknowns: before (x, y, z), after (x, y, z), m/s
    model[:2, :3] = jacobian
    model[2:, 3:] = jacobian
    bounds = [(-V_MAX_M_PER_S, V_MAX_M_PER_S)] * 6
    for sign in (1.0, -1.0):
        # sign * vy- >= min; |vy+| <= retention * sign * vy-.
        rules = np.zeros((3, 6))
        rules[0, 1] = -sign
        rules[1, 1], rules[1, 4] = -NET_ALONG_RETENTION_MAX * sign, sign
        rules[2, 1], rules[2, 4] = -NET_ALONG_RETENTION_MAX * sign, -sign
        a_ub = np.vstack([model, -model, rules])
        b_ub = np.concatenate([measured + tolerance, tolerance - measured, [-NET_MIN_ALONG_M_S, 0.0, 0.0]])
        if linprog(np.zeros(6), A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs").status == 0:
            return True
    return False


def _matching_pose_hit(
    event: BallEvent, pose_hits: Sequence[PoseHit], scale: float
) -> PoseHit | None:
    gate = POSE_HIT_WRIST_GATE_M * scale
    matches = [
        hit
        for hit in pose_hits
        if abs(hit.time_s - event.time_s) <= POSE_HIT_WINDOW_S
        and (hit.wrist_xy_px is None or math.dist(hit.wrist_xy_px, event.image_xy_px) <= gate)
    ]
    if not matches:
        return None
    return min(matches, key=lambda h: (abs(h.time_s - event.time_s), -h.confidence, h.time_s))


#: How far outside the doubles court a player may stand and still be mapped:
#: deep behind a baseline or wide of a sideline, not in the stands.
HITTER_RUNOFF_ALONG_M: Final[float] = 8.0
HITTER_RUNOFF_ACROSS_M: Final[float] = 4.0


def hitter_ground_point(
    calibration: CourtCalibration, feet_xy_px: tuple[float, float] | None
) -> tuple[float, float] | None:
    """The hitter's court position from the pose feet, or None when not
    measured. Only the feet: a wrist or the ball at contact is in the air,
    and the homography maps only ground points."""
    if feet_xy_px is None:
        return None
    ground = _ground_point(calibration, feet_xy_px)
    if ground is None:
        return None
    if abs(ground[0]) > DOUBLES_HALF_WIDTH_M + HITTER_RUNOFF_ACROSS_M or abs(ground[1]) > HALF_LENGTH_M + HITTER_RUNOFF_ALONG_M:
        return None
    return ground


def classify_event(
    event: BallEvent, calibration: CourtCalibration, pose_hits: Sequence[PoseHit] = ()
) -> BallEvent:
    """5.4 rules 1-5, with rule 3 (bounce) judged before rule 2 (net).

    Order taken here: pose hit, bounce, net, hit, unknown. From the camera the
    net hides part of the far court, so far bounces lie inside the image net
    polygon; judged first, rule 2 called 17 of 59 T0 bounces net contacts. A
    strike on the net has no upward impulse, so it cannot pass rule 3 first.
    Weak bounces gain the ``weak_bounce`` flag.
    """
    scale = _scale_at(calibration, event.image_xy_px)
    hit = _matching_pose_hit(event, pose_hits, scale)
    if hit is not None:
        return replace(
            event,
            kind=BallEventKind.HIT,
            end=hit.end,
            shot_type=hit.shot_type,
            hitter_feet_xy_px=hit.feet_xy_px,
            flags=_with_flag(event.flags, POSE_HIT_FLAG),
        )
    j_row = event.velocity_after_px_s[1] - event.velocity_before_px_s[1]
    if j_row < -J_MIN_M_S * scale:
        ground = _ground_point(calibration, event.image_xy_px)
        if (
            ground is not None
            and abs(ground[0]) <= DOUBLES_HALF_WIDTH_M + RUNOFF_M
            and abs(ground[1]) <= HALF_LENGTH_M + RUNOFF_M
            and bounce_is_consistent(event, calibration, ground)
        ):
            flags = event.flags
            if event.confidence < WEAK_BOUNCE_CONFIDENCE:
                flags = _with_flag(flags, WEAK_BOUNCE_FLAG)
            return replace(event, kind=BallEventKind.BOUNCE, flags=flags)
    if _inside_polygon(
        event.image_xy_px, net_polygon_px(calibration), NET_POLYGON_MARGIN_PX
    ) and net_contact_is_consistent(event, calibration):
        return replace(event, kind=BallEventKind.NET_CONTACT)
    if _speed_ratio(event) >= HIT_SPEED_RATIO_MIN or _horizontal_reverses(event, scale):
        return replace(event, kind=BallEventKind.HIT)
    return replace(event, kind=BallEventKind.UNKNOWN_KINK)


def _with_flag(flags: tuple[str, ...], flag: str) -> tuple[str, ...]:
    return flags if flag in flags else (*flags, flag)


def _without_flag(flags: tuple[str, ...], flag: str) -> tuple[str, ...]:
    return tuple(f for f in flags if f != flag)


# ----------------------------------------------------------------- grammar


def resolve_event_grammar(
    events: Sequence[BallEvent], calibration: CourtCalibration
) -> tuple[BallEvent, ...]:
    """Viterbi over {bounce, hit} for low-confidence bounces and hits, in time
    order. A relabel costs the event's confidence; a hit after a hit costs
    ``GRAMMAR_HIT_AFTER_HIT_COST`` (a volley) and a bounce after a bounce on
    the same half ``GRAMMAR_BOUNCE_AFTER_BOUNCE_COST`` (a point-ending double
    bounce). Pose hits, net contacts and unknown kinks are fixed. Never adds
    or removes an event; tie-breaks keep the original label."""
    ordered = sorted(events, key=lambda e: (e.time_s, e.flight_id, e.event_id))
    labels = (BallEventKind.BOUNCE, BallEventKind.HIT)

    def options(event: BallEvent) -> list[tuple[BallEventKind, float]]:
        free = (
            event.kind in labels
            and POSE_HIT_FLAG not in event.flags
            and event.confidence < GRAMMAR_RELABEL_MAX_CONFIDENCE
        )
        if not free:
            return [(event.kind, 0.0)]
        return [(event.kind, 0.0)] + [(k, event.confidence) for k in labels if k is not event.kind]

    def half(event: BallEvent) -> CourtEnd | None:
        ground = _ground_point(calibration, event.image_xy_px)
        return None if ground is None else half_at(ground[1])

    def transition(prev: BallEvent, prev_kind: BallEventKind, cur: BallEvent, kind: BallEventKind) -> float:
        if prev_kind is BallEventKind.HIT and kind is BallEventKind.HIT:
            return GRAMMAR_HIT_AFTER_HIT_COST
        if prev_kind is BallEventKind.BOUNCE and kind is BallEventKind.BOUNCE:
            same = half(prev) is not None and half(prev) == half(cur)
            return GRAMMAR_BOUNCE_AFTER_BOUNCE_COST if same else 2.0 * GRAMMAR_BOUNCE_AFTER_BOUNCE_COST
        return 0.0

    if not ordered:
        return ()
    # costs[i][k] = (best cost, back-pointer) for label option k of event i.
    choices = [options(e) for e in ordered]
    costs: list[list[tuple[float, int]]] = [[(c, -1) for _, c in choices[0]]]
    for i in range(1, len(ordered)):
        row: list[tuple[float, int]] = []
        for kind, emit in choices[i]:
            best = min(
                (
                    costs[i - 1][p][0] + transition(ordered[i - 1], prev_kind, ordered[i], kind) + emit,
                    p,
                )
                for p, (prev_kind, _) in enumerate(choices[i - 1])
            )
            row.append(best)
        costs.append(row)
    pick = min(range(len(costs[-1])), key=lambda k: (costs[-1][k][0], k))
    picks = [0] * len(ordered)
    for i in range(len(ordered) - 1, -1, -1):
        picks[i] = pick
        pick = costs[i][pick][1]
    resolved: list[BallEvent] = []
    for event, option, k in zip(ordered, choices, picks, strict=True):
        kind = option[k][0]
        if kind is event.kind:
            resolved.append(event)
            continue
        flags = _with_flag(event.flags, GRAMMAR_RELABELLED_FLAG)
        if kind is BallEventKind.BOUNCE and event.confidence < WEAK_BOUNCE_CONFIDENCE:
            flags = _with_flag(flags, WEAK_BOUNCE_FLAG)
        if kind is BallEventKind.HIT:
            flags = _without_flag(flags, WEAK_BOUNCE_FLAG)
        resolved.append(replace(event, kind=kind, flags=flags, end=None))
    return tuple(resolved)


# ------------------------------------------------------------------- driver


def _flight_start_hits(
    flights: Sequence[BallFlight], calibration: CourtCalibration, pose_hits: Sequence[PoseHit]
) -> list[BallEvent]:
    """A pose hit at a flight's first sample is that flight's launch."""
    events: list[BallEvent] = []
    for flight in flights:
        found = np.flatnonzero(flight.detected)
        if found.size < 2:
            continue
        first, second = found[0], found[1]
        t0 = float(flight.times_s[first])
        uv = (float(flight.xy_px[first, 0]), float(flight.xy_px[first, 1]))
        dt = float(flight.times_s[second] - flight.times_s[first])
        velocity = (flight.xy_px[second] - flight.xy_px[first]) / dt
        probe = BallEvent(
            event_id=f"tmp_{flight.flight_id}_start",
            flight_id=flight.flight_id,
            kind=BallEventKind.HIT,
            time_s=t0,
            time_sigma_s=dt / math.sqrt(12.0),
            image_xy_px=uv,
            image_cov_px2=np.eye(2) * FIT_SIGMA_FLOOR_PX**2,
            velocity_before_px_s=(math.nan, math.nan),
            velocity_after_px_s=(float(velocity[0]), float(velocity[1])),
            confidence=1.0,
            flags=(FLIGHT_START_FLAG, POSE_HIT_FLAG),
        )
        hit = _matching_pose_hit(probe, pose_hits, _scale_at(calibration, uv))
        if hit is not None:
            events.append(
                replace(
                    probe,
                    end=hit.end,
                    shot_type=hit.shot_type,
                    confidence=hit.confidence,
                    hitter_feet_xy_px=hit.feet_xy_px,
                )
            )
    return events


def _assign_hit_ends(events: Sequence[BallEvent], calibration: CourtCalibration) -> list[BallEvent]:
    """A HIT without a pose end takes the half of the previous bounce, else
    the opposite of the next bounce's half (time order)."""
    out = list(events)
    halves: list[CourtEnd | None] = []
    for event in out:
        if event.kind is BallEventKind.BOUNCE:
            ground = _ground_point(calibration, event.image_xy_px)
            halves.append(None if ground is None else half_at(ground[1]))
        else:
            halves.append(None)
    for i, event in enumerate(out):
        if event.kind is not BallEventKind.HIT or event.end is not None:
            continue
        end: CourtEnd | None = None
        for j in range(i - 1, -1, -1):
            if out[j].kind is BallEventKind.HIT:
                break
            if out[j].kind is BallEventKind.BOUNCE and halves[j] is not None:
                end = halves[j]
                break
        if end is None:
            for j in range(i + 1, len(out)):
                if out[j].kind is BallEventKind.HIT:
                    break
                if out[j].kind is BallEventKind.BOUNCE and halves[j] is not None:
                    end = CourtEnd.NEAR if halves[j] is CourtEnd.FAR else CourtEnd.FAR
                    break
        out[i] = replace(event, end=end)
    return out


def merge_split_bounces(
    events: Sequence[BallEvent], calibration: CourtCalibration
) -> list[BallEvent]:
    """Within one flight, a HIT not from pose followed within
    ``SPLIT_BOUNCE_MAX_GAP_S`` by an UNKNOWN_KINK is re-judged as ONE break:
    the hit's incoming velocity, the kink's outgoing one, at the kink's time
    and place. Kept merged only when that single break is a BOUNCE; otherwise
    both events stand. A net contact followed by its bounce is never merged."""
    ordered = sorted(events, key=lambda e: (e.flight_id, e.time_s, e.event_id))
    out: list[BallEvent] = []
    skip = False
    for index, first in enumerate(ordered):
        second = ordered[index + 1] if index + 1 < len(ordered) else None
        if skip:
            skip = False
            continue
        if (
            second is not None
            and first.flight_id == second.flight_id
            and first.kind is BallEventKind.HIT
            and POSE_HIT_FLAG not in first.flags
            and second.kind is BallEventKind.UNKNOWN_KINK
            and second.time_s - first.time_s <= SPLIT_BOUNCE_MAX_GAP_S
        ):
            joined = replace(
                second,
                velocity_before_px_s=first.velocity_before_px_s,
                confidence=max(first.confidence, second.confidence),
                flags=tuple(f for f in (*first.flags, *second.flags) if f != ONE_SIDED_BOUNCE_FLAG),
            )
            judged = classify_event(joined, calibration)
            if judged.kind is BallEventKind.BOUNCE:
                out.append(replace(judged, flags=_with_flag(judged.flags, SPLIT_BOUNCE_FLAG)))
                skip = True
                continue
        out.append(first)
    return out


def detect_ball_events(
    flights: Sequence[BallFlight],
    calibration: CourtCalibration,
    pose_hits: Sequence[PoseHit] = (),
) -> tuple[BallEvent, ...]:
    """All events over all flights, in time order, with final ids (brief G9):
    one running index across kinds, ``{b|h|n|k}_{index:06d}``."""
    raw: list[BallEvent] = []
    for flight in flights:
        raw.extend(classify_event(e, calibration, pose_hits) for e in find_break_points(flight))
    raw = merge_split_bounces(raw, calibration)
    raw.extend(_flight_start_hits(flights, calibration, pose_hits))
    resolved = resolve_event_grammar(raw, calibration)
    ordered = sorted(resolved, key=lambda e: (e.time_s, e.flight_id, e.event_id))
    ended = _assign_hit_ends(ordered, calibration)
    return tuple(
        replace(event, event_id=f"{_ID_PREFIX[event.kind]}_{index:06d}")
        for index, event in enumerate(ended)
    )
