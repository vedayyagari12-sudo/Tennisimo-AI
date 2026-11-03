"""Ball flight linking: per-frame candidates in, ball flights out (design 4.4).

PURE: numpy and stdlib only, no cv2, no I/O, no clock. Candidates arrive as
``FLIGHT_CANDIDATE_DTYPE`` records produced by ``app/ball/flight_detector.py``
(pure-CV), which imports the dtype from here and re-exports it (brief G4), so
the record layout is owned by the pure side of the seam.

Pixel units are TRACK_SPACE: rotated native frames capped at a 1920 px long
edge (design 2.3), y DOWN. Times are real PTS.

Built beside ``app/ball/track.associate_candidates``, not on it (design 4.1):
that tracker stops at a 35 degree turn, which is exactly the bounce this one
must survive. It reuses ``V_MAX_M_PER_S``, ``GATE_SHRINK_FACTOR``,
``STREAK_ORIENTATION_TOLERANCE_DEG`` and ``axis_difference_deg`` from there.

Link logic (4.4). Deterministic: every choice sorts on explicit keys, ending
in track id and candidate index.

1. Scale: ``scale_px_per_m(x, y)`` (air-plane px per metre, f / range). Max
   step = V_MAX x dt x scale x 1.5, dt from real PTS.
2. Seed: three candidates in consecutive frames, both steps inside the max
   step, the third within the extend gate of the straight-line prediction.
3. Extend: quadratic through the last 5 points since the last kink; gate =
   max(4 px, 0.35 x max step); nearest wins (one-to-one, globally sorted). A
   streak (aspect >= 1.6 with minor >= 3 px, the detector's tier-B shape)
   must lie within 25 degrees of its step.
4. Gaps: coast while the missing time is <= ``max_gap_s`` (0.1 s: 6 frames at
   60 fps, 3 at 30). Missing samples are NaN with ``detected`` False.
5. Reacquire: a track that finds nothing in its gate may take ONE wide step,
   anywhere within the max step of its last point; the motion model restarts
   there. This is what survives a bounce. No two wide steps in a row.
6. Stitch: tracklets whose gap is <= 0.2 s and whose next start lies within
   max step x gap are joined, greedily and one-to-one.
7. Reject: under 6 detections (under 4 below 45 fps), net displacement under
   30 px, or a fit RMS over 3 px across a 0.25 s stretch that a two-piece fit
   (a kink) does not explain.
8. Several balls may be in flight at once.

Decisions taken here (design 4.4 does not cover them):

* Step 7's fit is "quadratic in time, or the pinhole image of a 3D parabola,
  whichever fits better". A quadratic alone rejected real flights on T0: a
  ball leaving a near-camera hit goes from 4.7 m to 9.5 m depth in 0.15 s, so
  its image path is far from quadratic over 0.25 s.
* "Without a kink" is tested, not tracked: a window that fails is exempt when
  some split into two pieces fits. A short side (a hit while the ball is out
  of frame) is allowed; the other side must then carry the fit.
* ``stitch_tracklets`` takes ``frame_times_s`` and ``scale_px_per_m``: its
  own rule ("within max step x gap") needs the scale, and the gap's missing
  samples need frame times. The design 6.5 signature had neither.

Scaling and clutter (added after real handheld clips gave 80-2500 candidates
per frame; the first version's seeding was cubic in that count, and at 50
random candidates per frame a clutter track that happened to point at the
ball's first detection captured the whole flight, whose clutter head then made
step 7 reject it):

* Every gated neighbour search goes through a ``scipy.spatial.cKDTree`` per
  frame, and seeding is vectorised. A seed keeps, per (a, b) pair, the best
  valid c among its ``SEED_NEAREST_C`` nearest to the prediction (the first
  version enumerated every c; greedy selection then differs only when that
  best c is taken by a better seed).
* ``MAX_CANDIDATES_PER_FRAME`` is a deterministic backstop: above it, a frame
  keeps the candidates with the highest colour score, then contrast, then the
  lowest record index.
* Probation. A new seed, and a track's wide reacquire step, are on probation
  until ``PROBATION_POINTS`` detections from that step (the minimum flight
  length below 45 fps) fit one quadratic within ``SMOOTH_MAX_RMS_PX`` (step
  7's own limit) with a physically possible acceleration. Failing that, or closing or reaching the end of the data
  first, the probation detections are taken back (a seed dies; a wide step's
  track closes at the point before it), and those still inside the seeding
  window are offered to seeding again. Only verified pieces are stitched: a
  chain of unverified 3-5 point pieces has no 0.25 s window with 5 points,
  so step 7 could not reject it. A stitch whose joined flight fails step 7
  is undone and its pieces are judged on their own: a stray clutter point
  that a piece ends on is explained as a kink inside that piece but not once
  the next piece is joined after it. Verified tracks extend first, then tracks on probation, then
  seeding. A verified track's narrow step must also keep its last
  ``PREDICTION_POINTS`` points plus the new one within that quadratic limit;
  otherwise the step is refused, which sends a kink to the wide step and an
  ended flight to coasting instead of into clutter.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy.spatial import cKDTree

from app.ball.track import (
    GATE_SHRINK_FACTOR,
    STREAK_ORIENTATION_TOLERANCE_DEG,
    V_MAX_M_PER_S,
    axis_difference_deg,
)

__all__ = [
    "FLIGHT_CANDIDATE_DTYPE",
    "BallFlight",
    "cap_candidates_per_frame",
    "link_flights",
    "stitch_tracklets",
]

Float64Array = npt.NDArray[np.float64]
ScaleFn = Callable[[float, float], float]

#: Brief G4: 10 fields, packed, 56 bytes. ``orientation_deg`` is the major-axis
#: angle folded to [0, 180), y DOWN. ``colour_score`` is the fraction of the
#: component's pixels that pass the ball colour gate. ``contrast`` is the peak
#: double-difference strength over the component, in 8-bit grey levels.
FLIGHT_CANDIDATE_DTYPE: Final[np.dtype[np.void]] = np.dtype(
    [
        ("frame_index", "<i8"),
        ("time_s", "<f8"),
        ("x", "<f8"),
        ("y", "<f8"),
        ("area", "<f4"),
        ("minor", "<f4"),
        ("major", "<f4"),
        ("orientation_deg", "<f4"),
        ("colour_score", "<f4"),
        ("contrast", "<f4"),
    ]
)

#: 4.4 step 1: max step = V_MAX x dt x scale x 1.5.
MAX_STEP_SAFETY: Final[float] = 1.5
#: 4.4 step 3: gate = max(4 px, 0.35 x max step).
MIN_GATE_PX: Final[float] = 4.0
PREDICTION_POINTS: Final[int] = 5
#: Mirrors the detector's tier-B streak shape (``detector.py`` imports cv2, so
#: the numbers are restated here rather than imported).
STREAK_ASPECT_MIN: Final[float] = 1.6
STREAK_MINOR_MIN_PX: Final[float] = 3.0
#: 4.4 steps 4 and 6.
COAST_MAX_GAP_S: Final[float] = 0.1
STITCH_MAX_GAP_S: Final[float] = 0.2
#: 4.4 step 7.
MIN_DETECTIONS: Final[int] = 6
MIN_DETECTIONS_LOW_FPS: Final[int] = 4
LOW_FPS_BELOW: Final[float] = 45.0
MIN_NET_DISPLACEMENT_PX: Final[float] = 30.0
SMOOTH_WINDOW_S: Final[float] = 0.25
SMOOTH_MAX_RMS_PX: Final[float] = 3.0
#: A window needs this many detections before its fit means anything.
SMOOTH_MIN_POINTS: Final[int] = 5
#: The projected-parabola fit has 8 degrees of freedom; from 6 points it has
#: 4 spare equations.
PROJECTED_FIT_MIN_POINTS: Final[int] = 6
#: Backstop per-frame candidate cap (ranked by colour score, then contrast).
MAX_CANDIDATES_PER_FRAME: Final[int] = 256
#: Seeding keeps the best valid c among this many nearest to the prediction.
SEED_NEAREST_C: Final[int] = 4
#: Detections from a seed or a wide step that must fit one quadratic (capped
#: at the minimum flight length, so 4 below 45 fps).
PROBATION_POINTS: Final[int] = 6
#: Probation also bounds the fitted image acceleration (design 4.4 step 2's
#: "acceleration under limit"): ACCEL_LIMIT_M_S2 x scale px/s^2, plus a noise
#: floor of ACCEL_NOISE_PX_PER_FRAME2 per frame interval squared. 600 m/s^2 is
#: 1.6x the largest seen on T0 truth (368 m/s^2 over 24k 6-frame windows, 60
#: seeds of three scenarios): near the camera the ground scale under a ball
#: under-reads its true scale, so gravity plus drag (114 m/s^2) is not enough.
ACCEL_LIMIT_M_S2: Final[float] = 600.0
#: 3 sigma of the fitted acceleration for 1 px noise at 30 fps (4 points).
ACCEL_NOISE_PX_PER_FRAME2: Final[float] = 3.0
_TIME_EPSILON_S: Final[float] = 1e-9


@dataclass(frozen=True, eq=False)
class BallFlight:
    """One linked flight, one sample per frame from its first detection to its
    last. Missing samples have ``detected`` False and NaN position and size."""

    flight_id: int
    times_s: Float64Array
    xy_px: Float64Array
    detected: npt.NDArray[np.bool_]
    minor_axis_px: Float64Array
    #: Share of samples that are detections.
    confidence: float


@dataclass
class _Track:
    track_id: int
    #: (frame position, candidate index) per detection, in time order.
    detections: list[tuple[int, int]]
    #: Index into ``detections`` where the current motion model starts.
    model_start: int = 0
    wide_allowed: bool = True
    closed: bool = False
    history: list[tuple[float, float, float]] = field(default_factory=list)
    #: Passed probation; extends before tracks that have not.
    verified: bool = False
    #: Index into ``detections`` where the current probation began.
    probation_from: int = 0
    killed: bool = False


def _scale(scale_px_per_m: ScaleFn, x: float, y: float) -> float:
    value = float(scale_px_per_m(x, y))
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"scale_px_per_m({x:.1f}, {y:.1f}) returned {value}")
    return value


def _max_step_px(dt_s: float, scale: float) -> float:
    return V_MAX_M_PER_S * dt_s * scale * MAX_STEP_SAFETY


def _gate_px(max_step_px: float) -> float:
    return max(MIN_GATE_PX, GATE_SHRINK_FACTOR * max_step_px)


def _polyfit_eval(times: Float64Array, values: Float64Array, at: float, degree: int) -> float:
    origin = float(times[-1])
    basis = np.vander(times - origin, degree + 1)
    coefficients, *_ = np.linalg.lstsq(basis, values, rcond=None)
    return float(np.polyval(coefficients, at - origin))


def _predict(history: Sequence[tuple[float, float, float]], time_s: float) -> tuple[float, float]:
    """Quadratic (fewer points: lower degree) through the last 5 (t, x, y)."""
    recent = np.asarray(history[-PREDICTION_POINTS:], dtype=np.float64)
    degree = min(2, len(recent) - 1)
    if degree == 0:
        return float(recent[0, 1]), float(recent[0, 2])
    times = recent[:, 0]
    return (
        _polyfit_eval(times, recent[:, 1], time_s, degree),
        _polyfit_eval(times, recent[:, 2], time_s, degree),
    )


def _orientation_ok(candidate: np.void, from_xy: tuple[float, float]) -> bool:
    minor = float(candidate["minor"])
    major = float(candidate["major"])
    if minor < STREAK_MINOR_MIN_PX or major < STREAK_ASPECT_MIN * minor:
        return True
    dx = float(candidate["x"]) - from_xy[0]
    dy = float(candidate["y"]) - from_xy[1]
    if dx == 0.0 and dy == 0.0:
        return True
    step_deg = math.degrees(math.atan2(dy, dx))
    return axis_difference_deg(float(candidate["orientation_deg"]), step_deg) <= (
        STREAK_ORIENTATION_TOLERANCE_DEG
    )


def _fit_rms(times: Float64Array, xy: Float64Array, degree: int) -> float:
    origin = float(times[0])
    basis = np.vander(times - origin, degree + 1)
    coefficients, *_ = np.linalg.lstsq(basis, xy, rcond=None)
    residual = xy - basis @ coefficients
    return float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))


def _quadratic_rms(times: Float64Array, xy: Float64Array) -> float:
    return _fit_rms(times, xy, min(2, len(times) - 1))


def _projected_parabola_rms(times: Float64Array, xy: Float64Array) -> float:
    """RMS of the best image of a 3D parabola: u = A(t)/C(t), v = B(t)/C(t)
    with A, B, C quadratics (a pinhole view of drag-free flight). Solved
    linearly (u C - A = 0, v C - B = 0) on normalised data; ``inf`` when the
    fitted denominator vanishes or changes sign inside the window."""
    span = times - times.mean()
    t = span / max(float(np.max(np.abs(span))), _TIME_EPSILON_S)
    centre = xy.mean(axis=0)
    size = max(float(np.max(np.abs(xy - centre))), 1.0)
    p = (xy - centre) / size
    basis = np.column_stack([np.ones_like(t), t, t * t])
    zeros = np.zeros_like(basis)
    system = np.vstack(
        [
            np.hstack([basis, zeros, -p[:, :1] * basis]),
            np.hstack([zeros, basis, -p[:, 1:] * basis]),
        ]
    )
    solution = np.linalg.svd(system)[2][-1]
    denominator = basis @ solution[6:]
    if np.any(np.abs(denominator) < _TIME_EPSILON_S) or np.ptp(np.sign(denominator)) > 0:
        return math.inf
    fitted = np.column_stack([basis @ solution[:3], basis @ solution[3:6]]) / denominator[:, None]
    return float(np.sqrt(np.mean(np.sum(((fitted - p) * size) ** 2, axis=1))))


def _smooth_rms(times: Float64Array, xy: Float64Array) -> float:
    """Quadratic in time, or the projected parabola when that fits better and
    the window has the points for it (a ball near the camera changes depth
    fast, so its image path is a quadratic only far away)."""
    quadratic = _quadratic_rms(times, xy)
    if quadratic <= SMOOTH_MAX_RMS_PX or len(times) < PROJECTED_FIT_MIN_POINTS:
        return quadratic
    return min(quadratic, _projected_parabola_rms(times, xy))


def _piece_rms(times: Float64Array, xy: Float64Array) -> float:
    """One side of a kink, always with a spare degree of freedom: a line
    through 3 points, a quadratic through 4-5, either model from 6. 1-2 points
    cannot be judged and count as 0."""
    if len(times) <= 2:
        return 0.0
    if len(times) < PROJECTED_FIT_MIN_POINTS:
        return _fit_rms(times, xy, min(2, len(times) - 2))
    return _smooth_rms(times, xy)


def _kink_explains(times: Float64Array, xy: Float64Array) -> bool:
    """True when some split into two pieces fits within the limit. A side may
    be short (a kink near the window edge, or a hit while the ball is out of
    frame), but the other side must then carry the fit."""
    count = len(times)
    for split in range(1, count):
        left = _piece_rms(times[:split], xy[:split])
        right = _piece_rms(times[split:], xy[split:])
        combined = math.sqrt((left**2 * split + right**2 * (count - split)) / count)
        if combined <= SMOOTH_MAX_RMS_PX:
            return True
    return False


def _is_smooth(times: Float64Array, xy: Float64Array) -> bool:
    """4.4 step 7: no 0.25 s stretch fits a quadratic worse than 3 px RMS
    unless a single kink explains it."""
    count = len(times)
    for start in range(count):
        stop = start
        while stop < count and times[stop] - times[start] <= SMOOTH_WINDOW_S + _TIME_EPSILON_S:
            stop += 1
        if stop - start < SMOOTH_MIN_POINTS:
            continue
        window_t, window_xy = times[start:stop], xy[start:stop]
        if _smooth_rms(window_t, window_xy) > SMOOTH_MAX_RMS_PX and not _kink_explains(
            window_t, window_xy
        ):
            return False
    return True


def _median_dt(frame_times_s: Float64Array) -> float:
    deltas = np.diff(frame_times_s)
    deltas = deltas[np.isfinite(deltas) & (deltas > 0.0)]
    return float(np.median(deltas)) if deltas.size else 0.0


def _flight_from_samples(
    flight_id: int,
    frame_times_s: Float64Array,
    first: int,
    last: int,
    detections: dict[int, tuple[float, float, float]],
) -> BallFlight:
    """Samples for frame positions first..last; ``detections`` maps a frame
    position to (x, y, minor)."""
    positions = np.arange(first, last + 1)
    detected = np.array([int(p) in detections for p in positions], dtype=np.bool_)
    xy = np.full((len(positions), 2), np.nan)
    minor = np.full(len(positions), np.nan)
    for row, position in enumerate(positions):
        hit = detections.get(int(position))
        if hit is not None:
            xy[row] = hit[:2]
            minor[row] = hit[2]
    return BallFlight(
        flight_id=flight_id,
        times_s=np.asarray(frame_times_s[first : last + 1], dtype=np.float64),
        xy_px=xy,
        detected=detected,
        minor_axis_px=minor,
        confidence=float(np.mean(detected)),
    )


def _frame_positions(flight: BallFlight, frame_times_s: Float64Array) -> npt.NDArray[np.intp]:
    positions = np.searchsorted(frame_times_s, flight.times_s)
    if np.any(positions >= len(frame_times_s)) or not np.array_equal(
        frame_times_s[np.minimum(positions, len(frame_times_s) - 1)], flight.times_s
    ):
        raise ValueError("flight times are not frame times")
    return np.asarray(positions, dtype=np.intp)


def stitch_tracklets(
    tracklets: Sequence[BallFlight],
    frame_times_s: Float64Array,
    scale_px_per_m: ScaleFn,
    *,
    max_gap_s: float = STITCH_MAX_GAP_S,
) -> tuple[BallFlight, ...]:
    """Join tracklets across gaps of missing time <= ``max_gap_s`` when the
    next one starts within max step x gap of where the last one ended.

    Greedy and one-to-one over (gap, distance, earlier index, later index).
    Joined flights carry NaN, undetected samples over the gap. Flight ids are
    kept from the earliest tracklet of each chain; output is in input order of
    those earliest tracklets.
    """
    times = np.asarray(frame_times_s, dtype=np.float64)
    return tuple(
        _join(chain, tracklets, times)
        for chain in _stitch_chains(tracklets, times, scale_px_per_m, max_gap_s=max_gap_s)
    )


def _join(chain: Sequence[int], tracklets: Sequence[BallFlight], times: Float64Array) -> BallFlight:
    """One flight from a chain of tracklet indices (a single one is returned as is)."""
    if len(chain) == 1:
        return tracklets[chain[0]]
    samples: dict[int, tuple[float, float, float]] = {}
    for member in chain:
        flight = tracklets[member]
        for row, position in enumerate(_frame_positions(flight, times)):
            if flight.detected[row]:
                samples[int(position)] = (
                    float(flight.xy_px[row, 0]),
                    float(flight.xy_px[row, 1]),
                    float(flight.minor_axis_px[row]),
                )
    return _flight_from_samples(tracklets[chain[0]].flight_id, times, min(samples), max(samples), samples)


def _stitch_chains(
    tracklets: Sequence[BallFlight],
    times: Float64Array,
    scale_px_per_m: ScaleFn,
    *,
    max_gap_s: float = STITCH_MAX_GAP_S,
) -> list[list[int]]:
    """``stitch_tracklets``' chains of tracklet indices, in order of their heads."""
    frame_dt = _median_dt(times)
    ends: list[tuple[float, Float64Array]] = []
    starts: list[tuple[float, Float64Array]] = []
    for flight in tracklets:
        found = np.flatnonzero(flight.detected)
        if found.size == 0:
            raise ValueError("a tracklet has no detections")
        starts.append((float(flight.times_s[found[0]]), flight.xy_px[found[0]]))
        ends.append((float(flight.times_s[found[-1]]), flight.xy_px[found[-1]]))

    options: list[tuple[float, float, int, int]] = []
    start_times = np.array([t for t, _ in starts], dtype=np.float64)
    start_points = np.array([xy for _, xy in starts], dtype=np.float64).reshape(-1, 2)
    by_start = np.argsort(start_times, kind="stable")
    sorted_starts = start_times[by_start]
    for a, (end_t, end_xy) in enumerate(ends):
        # Only starts after this end and within the gap limit can join it.
        low = int(np.searchsorted(sorted_starts, end_t, side="right"))
        high = int(
            np.searchsorted(sorted_starts, end_t + max_gap_s + frame_dt + _TIME_EPSILON_S, side="right")
        )
        later = by_start[low:high]
        later = later[later != a]
        if later.size == 0:
            continue
        gap = start_times[later] - end_t
        valid = (gap > 0.0) & ~(gap - frame_dt > max_gap_s + _TIME_EPSILON_S)
        later, gap = later[valid], gap[valid]
        if later.size == 0:
            continue
        offset = start_points[later] - end_xy
        distance = np.hypot(offset[:, 0], offset[:, 1])
        reach = V_MAX_M_PER_S * MAX_STEP_SAFETY * _scale(scale_px_per_m, *end_xy) * gap
        within = distance <= reach
        options.extend(
            (float(g), float(d), a, int(b))
            for g, d, b in zip(gap[within], distance[within], later[within], strict=True)
        )
    successor: dict[int, int] = {}
    predecessor: dict[int, int] = {}
    for _, _, a, b in sorted(options):
        if a in successor or b in predecessor:
            continue
        successor[a] = b
        predecessor[b] = a

    chains: list[list[int]] = []
    for head in range(len(tracklets)):
        if head in predecessor:
            continue
        chain = [head]
        while chain[-1] in successor:
            chain.append(successor[chain[-1]])
        chains.append(chain)
    return chains


def _accepted_flights(
    chains: Sequence[Sequence[int]],
    tracklets: Sequence[BallFlight],
    times: Float64Array,
    min_detections: int,
) -> list[BallFlight]:
    """Step 7 over stitched chains. A stitch that makes the joined flight fail
    is undone: its pieces are judged on their own."""
    accepted: list[BallFlight] = []
    for chain in chains:
        joined = _join(chain, tracklets, times)
        if _accept(joined, min_detections):
            accepted.append(joined)
        elif len(chain) > 1:
            accepted.extend(
                tracklets[member] for member in chain if _accept(tracklets[member], min_detections)
            )
    return accepted


def _accept(flight: BallFlight, min_detections: int) -> bool:
    found = np.flatnonzero(flight.detected)
    if found.size < min_detections:
        return False
    first, last = flight.xy_px[found[0]], flight.xy_px[found[-1]]
    if float(np.hypot(*(last - first))) < MIN_NET_DISPLACEMENT_PX:
        return False
    return _is_smooth(flight.times_s[found], flight.xy_px[found])


def cap_candidates_per_frame(
    candidates: npt.NDArray[np.void], *, cap: int = MAX_CANDIDATES_PER_FRAME
) -> npt.NDArray[np.intp]:
    """Record indices kept by the per-frame cap, ascending.

    A frame with more than ``cap`` candidates keeps the ``cap`` with the
    highest ``colour_score``, then the highest ``contrast``, then the lowest
    record index. A NaN score ranks below every number. Deterministic.
    """
    if cap < 1:
        raise ValueError("cap must be at least 1")
    count = len(candidates)
    if count == 0:
        return np.zeros(0, dtype=np.intp)
    frame_index = np.asarray(candidates["frame_index"], dtype=np.int64)
    colour = np.nan_to_num(np.asarray(candidates["colour_score"], dtype=np.float64), nan=-np.inf)
    contrast = np.nan_to_num(np.asarray(candidates["contrast"], dtype=np.float64), nan=-np.inf)
    index = np.arange(count)
    order = np.lexsort((index, -contrast, -colour, frame_index))
    sorted_frames = frame_index[order]
    first_of_frame = np.r_[0, np.flatnonzero(np.diff(sorted_frames)) + 1]
    rank = index - np.repeat(first_of_frame, np.diff(np.r_[first_of_frame, count]))
    return np.sort(order[rank < cap]).astype(np.intp)


class _Candidates:
    """Column arrays of the candidate records (record access is slow)."""

    def __init__(self, candidates: npt.NDArray[np.void]) -> None:
        self.x = np.asarray(candidates["x"], dtype=np.float64)
        self.y = np.asarray(candidates["y"], dtype=np.float64)
        self.minor = np.asarray(candidates["minor"], dtype=np.float64)
        self.major = np.asarray(candidates["major"], dtype=np.float64)
        self.orientation = np.asarray(candidates["orientation_deg"], dtype=np.float64)

    def xy(self, index: int) -> tuple[float, float]:
        return float(self.x[index]), float(self.y[index])

    def points(self, indices: npt.NDArray[np.intp]) -> Float64Array:
        return np.column_stack([self.x[indices], self.y[indices]])

    def orientation_ok(
        self, indices: npt.NDArray[np.intp], from_x: npt.ArrayLike, from_y: npt.ArrayLike
    ) -> npt.NDArray[np.bool_]:
        """Vectorised ``_orientation_ok``: a streak must lie within 25 degrees
        of its step from (from_x, from_y); anything else passes."""
        minor = self.minor[indices]
        major = self.major[indices]
        streak = (minor >= STREAK_MINOR_MIN_PX) & (major >= STREAK_ASPECT_MIN * minor)
        dx = self.x[indices] - np.asarray(from_x, dtype=np.float64)
        dy = self.y[indices] - np.asarray(from_y, dtype=np.float64)
        step_deg = np.degrees(np.arctan2(dy, dx))
        delta = np.abs((self.orientation[indices] - step_deg) % 180.0)
        difference = np.minimum(delta, 180.0 - delta)
        still = (dx == 0.0) & (dy == 0.0)
        return np.asarray(~streak | still | (difference <= STREAK_ORIENTATION_TOLERANCE_DEG))


class _FrameIndex:
    """One frame's candidate indices and a KD tree over their points."""

    def __init__(self, indices: Sequence[int], columns: _Candidates) -> None:
        self.indices = np.asarray(sorted(indices), dtype=np.intp)
        self.tree = cKDTree(columns.points(self.indices)) if self.indices.size else None

    def within(self, centre: tuple[float, float], radius: float) -> npt.NDArray[np.intp]:
        """Candidate indices within ``radius`` of ``centre``, ascending."""
        if self.tree is None:
            return np.zeros(0, dtype=np.intp)
        hits = self.tree.query_ball_point(centre, radius)
        return np.sort(self.indices[np.asarray(hits, dtype=np.intp)])


def _quadratic_fits(points: Sequence[tuple[float, float, float]]) -> bool:
    window = np.asarray(points, dtype=np.float64)
    return _quadratic_rms(window[:, 0], window[:, 1:]) <= SMOOTH_MAX_RMS_PX


def _physically_plausible(
    points: Sequence[tuple[float, float, float]], scale_px_per_m: ScaleFn, frame_dt: float
) -> bool:
    """The fitted image acceleration of (t, x, y) points is one a ball can have."""
    window = np.asarray(points, dtype=np.float64)
    times = window[:, 0] - window[0, 0]
    coefficients, *_ = np.linalg.lstsq(np.vander(times, 3), window[:, 1:], rcond=None)
    acceleration = 2.0 * float(np.hypot(coefficients[0, 0], coefficients[0, 1]))
    centre = window[:, 1:].mean(axis=0)
    limit = ACCEL_LIMIT_M_S2 * _scale(scale_px_per_m, float(centre[0]), float(centre[1]))
    if frame_dt > 0.0:
        limit += ACCEL_NOISE_PX_PER_FRAME2 / frame_dt**2
    return acceleration <= limit


def _narrow_step_fits(track: _Track, now: float, xy: tuple[float, float]) -> bool:
    """A verified track's narrow step keeps its recent path one quadratic."""
    segment = track.history[track.model_start :]
    if not track.verified or len(segment) < PREDICTION_POINTS:
        return True
    return _quadratic_fits([*segment[-PREDICTION_POINTS:], (now, *xy)])


def link_flights(
    candidates: npt.NDArray[np.void],
    frame_times_s: Float64Array,
    scale_px_per_m: ScaleFn,
    *,
    max_gap_s: float = COAST_MAX_GAP_S,
) -> tuple[BallFlight, ...]:
    """Link candidate records into ball flights (4.4 steps 1-8).

    ``frame_times_s[i]`` is the PTS of frame ``i``; each candidate's
    ``frame_index`` indexes it. ``scale_px_per_m(x, y)`` is pixels per metre
    for a ball seen at image point (x, y). Flights are numbered 0.. in order of
    their first sample, then first position. Frames over
    ``MAX_CANDIDATES_PER_FRAME`` are capped first (``cap_candidates_per_frame``).

    Raises:
        ValueError: wrong dtype, a frame index out of range, frame times not
            increasing, or a scale that is not a positive finite number.
    """
    if candidates.dtype != FLIGHT_CANDIDATE_DTYPE:
        raise ValueError("candidates must be FLIGHT_CANDIDATE_DTYPE records")
    times = np.asarray(frame_times_s, dtype=np.float64)
    if times.ndim != 1 or (times.size > 1 and np.any(np.diff(times) <= 0.0)):
        raise ValueError("frame_times_s must be strictly increasing")
    frame_index = np.asarray(candidates["frame_index"], dtype=np.int64)
    if frame_index.size and (frame_index.min() < 0 or frame_index.max() >= times.size):
        raise ValueError("a candidate frame_index is outside frame_times_s")
    frame_dt = _median_dt(times)
    min_detections = MIN_DETECTIONS if frame_dt > 0.0 and 1.0 / frame_dt >= LOW_FPS_BELOW else (
        MIN_DETECTIONS_LOW_FPS
    )
    probation_points = min(PROBATION_POINTS, min_detections)
    columns = _Candidates(candidates)
    kept = cap_candidates_per_frame(candidates)
    per_frame: dict[int, list[int]] = {}
    for index in kept[np.argsort(frame_index[kept], kind="stable")]:
        per_frame.setdefault(int(frame_index[index]), []).append(int(index))

    tracks: list[_Track] = []
    active: list[_Track] = []
    unused: dict[int, list[int]] = {}
    for position in range(times.size):
        now = float(times[position])
        here = _FrameIndex(per_frame.get(position, []), columns)
        taken: set[int] = set()

        # Coast limit: close tracks whose missing time now exceeds the gap.
        live: list[tuple[_Track, float, tuple[float, float], tuple[float, float]]] = []
        for track in active:
            last_t, last_x, last_y = track.history[-1]
            if now - last_t - frame_dt > max_gap_s + _TIME_EPSILON_S:
                if track.verified:
                    track.closed = True
                else:
                    _revert_probation(track)  # unfinished: too old to reseed
                continue
            max_step = _max_step_px(now - last_t, _scale(scale_px_per_m, last_x, last_y))
            live.append((track, max_step, _predict(track.history[track.model_start :], now), (last_x, last_y)))

        # Verified tracks first, then tracks on probation.
        assigned: set[int] = set()
        stepped: list[_Track] = []
        for verified in (True, False):
            tier = [entry for entry in live if entry[0].verified is verified]
            stepped.extend(_extend_tier(tier, here, columns, now, position, taken, assigned))

        # Probation: judge, and give failed detections back to seeding.
        released: list[tuple[int, int]] = []
        for track in stepped:
            if not track.verified and len(track.detections) - track.probation_from >= probation_points:
                probation = track.history[track.probation_from :]
                if _quadratic_fits(probation) and _physically_plausible(probation, scale_px_per_m, frame_dt):
                    track.verified = True
                else:
                    released.extend(_revert_probation(track))
        for frame, index in released:
            if frame == position:
                taken.discard(index)
            elif frame >= position - 2 and frame in unused:
                unused[frame] = sorted({*unused[frame], index})

        # Seed from unused candidates in three consecutive frames.
        unused[position] = [int(i) for i in here.indices if int(i) not in taken]
        unused.pop(position - 3, None)
        if position >= 2:
            tracks.extend(_seed(columns, unused, position, times, scale_px_per_m, len(tracks)))
        active = [t for t in tracks if not t.closed and not t.killed]
    for track in active:
        if not track.verified:
            _revert_probation(track)  # the data ended before the verdict

    tracklets = [
        _flight_from_samples(
            track.track_id,
            times,
            track.detections[0][0],
            track.detections[-1][0],
            {
                position: (*columns.xy(index), float(columns.minor[index]))
                for position, index in track.detections
            },
        )
        for track in tracks
        if not track.killed
    ]
    accepted = _accepted_flights(
        _stitch_chains(tracklets, times, scale_px_per_m), tracklets, times, min_detections
    )
    accepted.sort(
        key=lambda f: (
            float(f.times_s[0]),
            float(f.xy_px[np.flatnonzero(f.detected)[0], 0]),
            float(f.xy_px[np.flatnonzero(f.detected)[0], 1]),
        )
    )
    return tuple(
        BallFlight(
            flight_id=number,
            times_s=f.times_s,
            xy_px=f.xy_px,
            detected=f.detected,
            minor_axis_px=f.minor_axis_px,
            confidence=f.confidence,
        )
        for number, f in enumerate(accepted)
    )


def _extend_tier(
    tier: Sequence[tuple[_Track, float, tuple[float, float], tuple[float, float]]],
    here: _FrameIndex,
    columns: _Candidates,
    now: float,
    position: int,
    taken: set[int],
    assigned: set[int],
) -> list[_Track]:
    """Narrow steps, then wide reacquire steps, for one priority tier.
    Returns the tracks that took a detection."""
    by_id = {track.track_id: track for track, *_ in tier}
    stepped: list[_Track] = []

    def take(track: _Track, index: int) -> None:
        track.detections.append((position, index))
        track.history.append((now, *columns.xy(index)))
        assigned.add(track.track_id)
        taken.add(index)
        stepped.append(track)

    # Extend: narrow gates, one-to-one by (distance, track id, candidate).
    pairs: list[tuple[float, int, int]] = []
    for track, max_step, predicted, last_xy in tier:
        gate = _gate_px(max_step)
        found = here.within(predicted, gate)
        if found.size == 0:
            continue
        distance = np.hypot(columns.x[found] - predicted[0], columns.y[found] - predicted[1])
        ok = (distance <= gate) & columns.orientation_ok(found, last_xy[0], last_xy[1])
        pairs.extend(
            (float(d), track.track_id, int(i)) for d, i in zip(distance[ok], found[ok], strict=True)
        )
    for _, track_id, index in sorted(pairs):
        if track_id in assigned or index in taken:
            continue
        track = by_id[track_id]
        if not _narrow_step_fits(track, now, columns.xy(index)):
            continue
        take(track, index)
        track.wide_allowed = True

    # Reacquire: one wide step within the max step of the last point.
    wide: list[tuple[float, int, int]] = []
    for track, max_step, predicted, last_xy in tier:
        if track.track_id in assigned or not track.wide_allowed:
            continue
        found = here.within(last_xy, max_step)
        if found.size == 0:
            continue
        reach = np.hypot(columns.x[found] - last_xy[0], columns.y[found] - last_xy[1])
        ok = (reach <= max_step) & columns.orientation_ok(found, last_xy[0], last_xy[1])
        found = found[ok]
        distance = np.hypot(columns.x[found] - predicted[0], columns.y[found] - predicted[1])
        wide.extend(
            (float(d), track.track_id, int(i))
            for d, i in zip(distance, found, strict=True)
            if int(i) not in taken
        )
    for _, track_id, index in sorted(wide):
        if track_id in assigned or index in taken:
            continue
        track = by_id[track_id]
        track.model_start = len(track.history) - 1
        if track.verified:
            track.verified = False
            track.probation_from = len(track.detections)
        take(track, index)
        track.wide_allowed = False
    return stepped


def _revert_probation(track: _Track) -> list[tuple[int, int]]:
    """Take back a failed probation's detections. A seed dies; a track whose
    wide step failed closes at the point before it. Returns the detections."""
    start = track.probation_from
    released = track.detections[start:]
    del track.detections[start:]
    del track.history[start:]
    if track.detections:
        track.closed = True
        track.verified = True
        track.model_start = min(track.model_start, len(track.history) - 1)
    else:
        track.killed = True
    return released


def _seed(
    columns: _Candidates,
    unused: dict[int, list[int]],
    position: int,
    times: Float64Array,
    scale_px_per_m: ScaleFn,
    next_id: int,
) -> list[_Track]:
    """Triplets (a, b, c) over frames position-2..position, greedy by
    (residual, a, b, c), each candidate used once. Per (a, b) only the best
    valid c among the ``SEED_NEAREST_C`` nearest to the prediction is
    offered. Consumed candidates are removed from ``unused``."""
    first = np.asarray(unused.get(position - 2, []), dtype=np.intp)
    middle = np.asarray(unused.get(position - 1, []), dtype=np.intp)
    last = np.asarray(unused.get(position, []), dtype=np.intp)
    if first.size == 0 or middle.size == 0 or last.size == 0:
        return []
    t0, t1, t2 = (float(times[position - k]) for k in (2, 1, 0))
    scale = np.array([_scale(scale_px_per_m, *columns.xy(int(a))) for a in first])
    step_ab = V_MAX_M_PER_S * (t1 - t0) * scale * MAX_STEP_SAFETY
    step_bc = V_MAX_M_PER_S * (t2 - t1) * scale * MAX_STEP_SAFETY

    # (a, b) pairs: b within the max step of a, streak along its step.
    neighbours = cKDTree(columns.points(middle)).query_ball_point(columns.points(first), step_ab)
    row = np.repeat(np.arange(first.size), [len(n) for n in neighbours])
    if row.size == 0:
        return []
    a = first[row]
    b = middle[np.concatenate([np.asarray(n, dtype=np.intp) for n in neighbours])]
    ax, ay, bx, by = columns.x[a], columns.y[a], columns.x[b], columns.y[b]
    ok = (np.hypot(bx - ax, by - ay) <= step_ab[row]) & columns.orientation_ok(b, ax, ay)
    row, a, b = row[ok], a[ok], b[ok]
    if row.size == 0:
        return []
    ax, ay, bx, by = columns.x[a], columns.y[a], columns.x[b], columns.y[b]
    ratio = (t2 - t1) / (t1 - t0)
    predicted = np.column_stack([bx + (bx - ax) * ratio, by + (by - ay) * ratio])

    # Best valid c among the nearest to each prediction.
    k = min(SEED_NEAREST_C, last.size)
    _, nearest = cKDTree(columns.points(last)).query(predicted, k=k)
    nearest = np.asarray(nearest, dtype=np.intp).reshape(len(predicted), k)
    gate = np.maximum(MIN_GATE_PX, GATE_SHRINK_FACTOR * step_bc[row])
    options: list[tuple[float, int, int, int]] = []
    chosen = np.full(len(predicted), -1, dtype=np.intp)
    residual = np.full(len(predicted), np.inf)
    for column in range(k):
        c = last[nearest[:, column]]
        cx, cy = columns.x[c], columns.y[c]
        distance = np.hypot(cx - predicted[:, 0], cy - predicted[:, 1])
        valid = (
            (chosen < 0)
            & (np.hypot(cx - bx, cy - by) <= step_bc[row])
            & columns.orientation_ok(c, bx, by)
            & (distance <= gate)
        )
        chosen[valid] = c[valid]
        residual[valid] = distance[valid]
    found = chosen >= 0
    options = sorted(
        zip(residual[found].tolist(), a[found].tolist(), b[found].tolist(), chosen[found].tolist(), strict=True)
    )
    seeded: list[_Track] = []
    used: set[int] = set()
    for _, ia, ib, ic in options:
        if ia in used or ib in used or ic in used:
            continue
        used.update((ia, ib, ic))
        track = _Track(
            track_id=next_id + len(seeded),
            detections=[(position - 2, ia), (position - 1, ib), (position, ic)],
        )
        track.history = [(t0, *columns.xy(ia)), (t1, *columns.xy(ib)), (t2, *columns.xy(ic))]
        seeded.append(track)
    for frame in (position - 2, position - 1, position):
        unused[frame] = [i for i in unused.get(frame, []) if i not in used]
    return seeded
