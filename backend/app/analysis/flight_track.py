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
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

import numpy as np
import numpy.typing as npt

from app.ball.track import (
    GATE_SHRINK_FACTOR,
    STREAK_ORIENTATION_TOLERANCE_DEG,
    V_MAX_M_PER_S,
    axis_difference_deg,
)

__all__ = [
    "FLIGHT_CANDIDATE_DTYPE",
    "BallFlight",
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
    for a, (end_t, end_xy) in enumerate(ends):
        for b, (start_t, start_xy) in enumerate(starts):
            gap = start_t - end_t
            if a == b or gap <= 0.0 or gap - frame_dt > max_gap_s + _TIME_EPSILON_S:
                continue
            distance = float(np.hypot(*(start_xy - end_xy)))
            reach = V_MAX_M_PER_S * MAX_STEP_SAFETY * _scale(scale_px_per_m, *end_xy) * gap
            if distance <= reach:
                options.append((gap, distance, a, b))
    successor: dict[int, int] = {}
    predecessor: dict[int, int] = {}
    for _, _, a, b in sorted(options):
        if a in successor or b in predecessor:
            continue
        successor[a] = b
        predecessor[b] = a

    stitched: list[BallFlight] = []
    for head in range(len(tracklets)):
        if head in predecessor:
            continue
        chain = [head]
        while chain[-1] in successor:
            chain.append(successor[chain[-1]])
        if len(chain) == 1:
            stitched.append(tracklets[head])
            continue
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
        stitched.append(
            _flight_from_samples(
                tracklets[head].flight_id, times, min(samples), max(samples), samples
            )
        )
    return tuple(stitched)


def _accept(flight: BallFlight, min_detections: int) -> bool:
    found = np.flatnonzero(flight.detected)
    if found.size < min_detections:
        return False
    first, last = flight.xy_px[found[0]], flight.xy_px[found[-1]]
    if float(np.hypot(*(last - first))) < MIN_NET_DISPLACEMENT_PX:
        return False
    return _is_smooth(flight.times_s[found], flight.xy_px[found])


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
    their first sample, then first position.

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
    order = np.lexsort((np.arange(len(candidates)), frame_index))
    per_frame: dict[int, list[int]] = {}
    for index in order:
        per_frame.setdefault(int(frame_index[index]), []).append(int(index))

    def xy_of(index: int) -> tuple[float, float]:
        return float(candidates[index]["x"]), float(candidates[index]["y"])

    tracks: list[_Track] = []
    unused: dict[int, list[int]] = {}
    for position in range(times.size):
        now = float(times[position])
        here = per_frame.get(position, [])
        taken: set[int] = set()
        active = [t for t in tracks if not t.closed]

        # Coast limit: close tracks whose missing time now exceeds the gap.
        live: list[tuple[_Track, float, tuple[float, float], tuple[float, float]]] = []
        for track in active:
            last_t, last_x, last_y = track.history[-1]
            if now - last_t - frame_dt > max_gap_s + _TIME_EPSILON_S:
                track.closed = True
                continue
            max_step = _max_step_px(now - last_t, _scale(scale_px_per_m, last_x, last_y))
            live.append((track, max_step, _predict(track.history[track.model_start :], now), (last_x, last_y)))

        # Extend: narrow gates, one-to-one by (distance, track id, candidate).
        pairs: list[tuple[float, int, int]] = []
        for track, max_step, predicted, last_xy in live:
            gate = _gate_px(max_step)
            for index in here:
                x, y = xy_of(index)
                distance = math.hypot(x - predicted[0], y - predicted[1])
                if distance <= gate and _orientation_ok(candidates[index], last_xy):
                    pairs.append((distance, track.track_id, index))
        assigned: set[int] = set()
        by_id = {track.track_id: track for track, *_ in live}
        for _, track_id, index in sorted(pairs):
            if track_id in assigned or index in taken:
                continue
            track = by_id[track_id]
            track.detections.append((position, index))
            track.history.append((now, *xy_of(index)))
            track.wide_allowed = True
            assigned.add(track_id)
            taken.add(index)

        # Reacquire: one wide step within the max step of the last point.
        wide: list[tuple[float, int, int]] = []
        for track, max_step, predicted, last_xy in live:
            if track.track_id in assigned or not track.wide_allowed:
                continue
            for index in here:
                if index in taken:
                    continue
                x, y = xy_of(index)
                if math.hypot(x - last_xy[0], y - last_xy[1]) <= max_step and _orientation_ok(
                    candidates[index], last_xy
                ):
                    wide.append((math.hypot(x - predicted[0], y - predicted[1]), track.track_id, index))
        for _, track_id, index in sorted(wide):
            if track_id in assigned or index in taken:
                continue
            track = by_id[track_id]
            track.model_start = len(track.history) - 1
            track.detections.append((position, index))
            track.history.append((now, *xy_of(index)))
            track.wide_allowed = False
            assigned.add(track_id)
            taken.add(index)

        # Seed from unused candidates in three consecutive frames.
        unused[position] = [i for i in here if i not in taken]
        unused.pop(position - 3, None)
        if position >= 2:
            tracks.extend(
                _seed(candidates, unused, position, times, scale_px_per_m, len(tracks), xy_of)
            )

    min_detections = MIN_DETECTIONS if frame_dt > 0.0 and 1.0 / frame_dt >= LOW_FPS_BELOW else (
        MIN_DETECTIONS_LOW_FPS
    )
    tracklets = [
        _flight_from_samples(
            track.track_id,
            times,
            track.detections[0][0],
            track.detections[-1][0],
            {
                position: (*xy_of(index), float(candidates[index]["minor"]))
                for position, index in track.detections
            },
        )
        for track in tracks
    ]
    joined = stitch_tracklets(tracklets, times, scale_px_per_m)
    kept = [flight for flight in joined if _accept(flight, min_detections)]
    kept.sort(
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
        for number, f in enumerate(kept)
    )


def _seed(
    candidates: npt.NDArray[np.void],
    unused: dict[int, list[int]],
    position: int,
    times: Float64Array,
    scale_px_per_m: ScaleFn,
    next_id: int,
    xy_of: Callable[[int], tuple[float, float]],
) -> list[_Track]:
    """Triplets (a, b, c) over frames position-2..position, greedy by
    (residual, a, b, c), each candidate used once. Consumed candidates are
    removed from ``unused``."""
    t0, t1, t2 = (float(times[position - k]) for k in (2, 1, 0))
    options: list[tuple[float, int, int, int]] = []
    for a in unused.get(position - 2, []):
        ax, ay = xy_of(a)
        scale = _scale(scale_px_per_m, ax, ay)
        step_ab = _max_step_px(t1 - t0, scale)
        step_bc = _max_step_px(t2 - t1, scale)
        for b in unused.get(position - 1, []):
            bx, by = xy_of(b)
            if math.hypot(bx - ax, by - ay) > step_ab or not _orientation_ok(candidates[b], (ax, ay)):
                continue
            ratio = (t2 - t1) / (t1 - t0)
            px, py = bx + (bx - ax) * ratio, by + (by - ay) * ratio
            for c in unused.get(position, []):
                cx, cy = xy_of(c)
                if math.hypot(cx - bx, cy - by) > step_bc or not _orientation_ok(candidates[c], (bx, by)):
                    continue
                residual = math.hypot(cx - px, cy - py)
                if residual <= _gate_px(step_bc):
                    options.append((residual, a, b, c))
    seeded: list[_Track] = []
    used: set[int] = set()
    for _, a, b, c in sorted(options):
        if a in used or b in used or c in used:
            continue
        used.update((a, b, c))
        track = _Track(
            track_id=next_id + len(seeded),
            detections=[(position - 2, a), (position - 1, b), (position, c)],
        )
        track.history = [(t0, *xy_of(a)), (t1, *xy_of(b)), (t2, *xy_of(c))]
        seeded.append(track)
    for frame in (position - 2, position - 1, position):
        unused[frame] = [i for i in unused.get(frame, []) if i not in used]
    return seeded
