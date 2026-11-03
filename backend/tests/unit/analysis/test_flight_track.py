"""Build step 9: ``analysis/flight_track.py`` (design 4.4).

Done when: it survives a bounce, unlike ``associate_candidates`` (both
asserted); links across a 0.1 s gap; rejects noise; keeps two simultaneous
balls separate; and the same input gives identical output.

T0 tier (design 11.1): court_synth truth projected to candidate records with
Gaussian pixel noise, no pixels rendered.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pytest

from app.analysis import flight_track
from app.analysis.court_calibration import CourtTap, calibrate_court
from app.analysis.court_model import CourtKeypoint, keypoint_xy
from app.analysis.flight_track import (
    FLIGHT_CANDIDATE_DTYPE,
    BallFlight,
    link_flights,
    stitch_tracklets,
)
from app.analysis.homography import image_scale_px_per_m
from app.ball.track import (
    TERMINATED_DIRECTION_CHANGE,
    TIER_ROUND,
    BallCandidate,
    ContourFeatures,
    associate_candidates,
)
from tests.unit.court_synth.camera import SynthCamera, default_camera
from tests.unit.court_synth.scene import BallRole, SceneTruth, generate_rally

SEED = 20261102
FPS = 60.0
SCALE = 60.0  # px per metre, for hand-made image-plane paths


def constant_scale(x: float, y: float) -> float:
    return SCALE


def records(frame_index: npt.ArrayLike, frame_times: npt.NDArray[np.float64], xy: npt.ArrayLike,
            *, minor: float = 2.0, major: float = 2.0, orientation: float = 0.0) -> npt.NDArray[np.void]:
    index = np.asarray(frame_index, dtype=np.int64)
    points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    out = np.zeros(len(index), dtype=FLIGHT_CANDIDATE_DTYPE)
    out["frame_index"] = index
    out["time_s"] = frame_times[index]
    out["x"], out["y"] = points[:, 0], points[:, 1]
    out["area"] = 4.0
    out["minor"], out["major"], out["orientation_deg"] = minor, major, orientation
    return out


def parabola(times: npt.NDArray[np.float64], start: tuple[float, float], velocity: tuple[float, float],
             gravity_px: float = 600.0) -> npt.NDArray[np.float64]:
    t = times - times[0]
    return np.column_stack([start[0] + velocity[0] * t, start[1] + velocity[1] * t + 0.5 * gravity_px * t * t])


def frame_times(count: int, fps: float = FPS) -> npt.NDArray[np.float64]:
    return np.arange(count, dtype=np.float64) / fps


def merged(*parts: npt.NDArray[np.void]) -> npt.NDArray[np.void]:
    joined = np.concatenate(parts)
    return joined[np.argsort(joined["frame_index"], kind="stable")]


# ------------------------------------------------------------- T0 helpers


def scale_for(camera: SynthCamera) -> Callable[[float, float], float]:
    keypoints = list(CourtKeypoint)
    uv = camera.project(np.array([[*keypoint_xy(k), 0.0] for k in keypoints]))
    taps = [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(keypoints, uv, strict=True)]
    calibration = calibrate_court(taps, camera.frame_size).calibration
    assert calibration is not None
    return lambda x, y: image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, (x, y))


def t0_records(scene: SceneTruth, camera: SynthCamera, seed: int, noise_px: float = 0.5
               ) -> tuple[npt.NDArray[np.void], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Main-ball truth at 60 fps, in frame, with pixel noise. Returns
    (records, frame times, truth uv per frame with NaN off-frame)."""
    ball = next(b for b in scene.balls if b.role is BallRole.MAIN)
    times = frame_times(int(scene.duration_s * FPS))
    uv = camera.project(ball.trajectory.position_at(times))
    width, height = camera.frame_size
    inside = (times >= ball.trajectory.start_s) & (times <= ball.trajectory.end_s)
    ok = inside & np.all(np.isfinite(uv), axis=1)
    ok &= (uv[:, 0] >= 0) & (uv[:, 0] < width) & (uv[:, 1] >= 0) & (uv[:, 1] < height)
    uv[~ok] = np.nan
    index = np.flatnonzero(ok)
    rng = np.random.default_rng(seed)
    return records(index, times, uv[index] + rng.normal(0.0, noise_px, (len(index), 2))), times, uv


def flight_containing(flights: tuple[BallFlight, ...], time_s: float) -> BallFlight | None:
    for flight in flights:
        if flight.times_s[0] < time_s < flight.times_s[-1]:
            return flight
    return None


# ------------------------------------------------- survives a bounce (T0)


@pytest.mark.parametrize("seed", range(SEED, SEED + 6))
def test_t0_serve_bounce_survived_where_associate_candidates_stops(seed: int) -> None:
    scene = generate_rally(seed)
    camera = default_camera((1920, 1080))
    scale = scale_for(camera)
    candidates, times, _ = t0_records(scene, camera, seed)
    rally = scene.rallies[0]
    serve_s, bounce_s, return_s = rally.hits[0].time_s, rally.bounces[0].time_s, rally.hits[1].time_s

    # Existing tracker over the serve, seeded at the first frame of it.
    window = [int(i) for i in candidates["frame_index"] if serve_s <= times[i] < return_s]
    by_frame = [
        [
            BallCandidate(
                i - window[0],
                ContourFeatures(float(r["x"]), float(r["y"]), 4.0, 7.0, 0.9, 2.0, 2.0, 1.0, 1.0, 1.0, 0.0),
                TIER_ROUND,
            )
            for r in candidates[candidates["frame_index"] == i]
        ]
        for i in range(window[0], window[-1] + 1)
    ]
    existing = associate_candidates(
        by_frame, (1080, 1920), fps=FPS, px_per_m=scale(float(candidates["x"][0]), float(candidates["y"][0]))
    )
    assert existing.track is not None
    assert existing.track.terminated_by == TERMINATED_DIRECTION_CHANGE
    last_s = times[window[0] + int(existing.track.frame_offsets[-1])]
    assert last_s <= bounce_s + 1.0 / FPS

    # Ours: one flight with detections on both sides of the bounce.
    flights = link_flights(candidates, times, scale)
    flight = flight_containing(flights, bounce_s)
    assert flight is not None
    found = flight.times_s[flight.detected]
    assert np.any((found < bounce_s) & (found >= bounce_s - 3.0 / FPS))
    assert np.any((found > bounce_s) & (found <= bounce_s + 3.0 / FPS))
    assert found[0] <= serve_s + 3.0 / FPS
    assert found[-1] >= return_s - 3.0 / FPS


@pytest.mark.parametrize("seed", range(SEED, SEED + 6))
def test_t0_every_in_frame_bounce_is_inside_a_flight(seed: int) -> None:
    scene = generate_rally(seed)
    camera = default_camera((1920, 1080))
    candidates, times, truth_uv = t0_records(scene, camera, seed)
    flights = link_flights(candidates, times, scale_for(camera))
    checked = 0
    for bounce in scene.bounces:
        frame = int(round(bounce.time_s * FPS))
        if not np.all(np.isfinite(truth_uv[frame - 3 : frame + 4])):
            continue  # off-frame near the bounce
        checked += 1
        assert flight_containing(flights, bounce.time_s) is not None, bounce
    assert checked >= 2
    # Every detected sample is the true ball, never interpolated.
    for flight in flights:
        frames = np.round(flight.times_s[flight.detected] * FPS).astype(int)
        assert np.max(np.hypot(*(flight.xy_px[flight.detected] - truth_uv[frames]).T)) < 3.0
        assert np.all(np.isnan(flight.xy_px[~flight.detected]))


def test_clean_image_plane_bounce_is_one_flight() -> None:
    """A 108 degree image turn: direction (6, 10) px/frame, then (6, -7)."""
    times = frame_times(40)
    steps = np.array([(6.0, 10.0)] * 20 + [(6.0, -7.0)] * 19)
    xy = np.vstack([[(300.0, 200.0)], (300.0, 200.0) + np.cumsum(steps, axis=0)])
    flights = link_flights(records(np.arange(40), times, xy), times, constant_scale)
    assert len(flights) == 1
    assert int(flights[0].detected.sum()) == 40


# ---------------------------------------------------------------- gaps


@pytest.mark.parametrize(("fps", "missing"), [(60.0, 6), (30.0, 3)])
def test_links_across_a_point_one_second_gap(fps: float, missing: int) -> None:
    count = int(1.2 * fps)
    times = frame_times(count, fps)
    xy = parabola(times, (200.0, 600.0), (700.0, -500.0))
    keep = np.ones(count, dtype=bool)
    keep[20 : 20 + missing] = False
    flights = link_flights(records(np.flatnonzero(keep), times, xy[keep]), times, constant_scale)
    assert len(flights) == 1
    flight = flights[0]
    assert np.array_equal(flight.times_s, times)
    assert np.array_equal(flight.detected, keep)
    assert np.all(np.isnan(flight.xy_px[~keep])) and np.all(np.isnan(flight.minor_axis_px[~keep]))
    assert np.allclose(flight.xy_px[keep], xy[keep])
    assert flight.confidence == pytest.approx(keep.mean())


def test_a_quarter_second_gap_splits_the_flight() -> None:
    times = frame_times(90)
    xy = parabola(times, (200.0, 600.0), (700.0, -500.0))
    keep = np.ones(90, dtype=bool)
    keep[40:55] = False  # 0.25 s missing: over the coast and the stitch limits
    flights = link_flights(records(np.flatnonzero(keep), times, xy[keep]), times, constant_scale)
    assert len(flights) == 2
    assert flights[0].times_s[-1] < flights[1].times_s[0]


def flight_of(times: npt.NDArray[np.float64], first: int, xy: npt.NDArray[np.float64], flight_id: int) -> BallFlight:
    count = len(xy)
    return BallFlight(
        flight_id=flight_id,
        times_s=times[first : first + count],
        xy_px=xy,
        detected=np.ones(count, dtype=bool),
        minor_axis_px=np.full(count, 2.0),
        confidence=1.0,
    )


def test_stitch_joins_within_point_two_seconds_and_reach() -> None:
    times = frame_times(100)
    path = parabola(times, (100.0, 500.0), (600.0, -300.0))
    a = flight_of(times, 0, path[0:30], 7)
    near = flight_of(times, 40, path[40:70], 8)  # 9 missing = 0.15 s
    # Reach = 71.5 m/s x 1.5 x 60 px/m x 0.167 s = 1073 px; 1500 px is beyond it.
    far_off = flight_of(times, 40, path[40:70] + (1500.0, 0.0), 9)
    late = flight_of(times, 45, path[45:75], 10)  # 14 missing = 0.233 s
    (joined,) = stitch_tracklets([a, near], times, constant_scale)
    assert joined.flight_id == 7
    assert np.array_equal(joined.times_s, times[0:70])
    assert int(joined.detected.sum()) == 60 and not joined.detected[30:40].any()
    assert len(stitch_tracklets([a, far_off], times, constant_scale)) == 2
    assert len(stitch_tracklets([a, late], times, constant_scale)) == 2


def test_stitch_is_one_to_one_and_prefers_the_shorter_gap() -> None:
    times = frame_times(100)
    path = parabola(times, (100.0, 500.0), (600.0, -300.0))
    a = flight_of(times, 0, path[0:30], 0)
    soon = flight_of(times, 33, path[33:60], 1)
    later = flight_of(times, 38, path[38:64] + (0.0, 1.0), 2)
    stitched = stitch_tracklets([a, soon, later], times, constant_scale)
    assert [f.flight_id for f in stitched] == [0, 2]
    assert stitched[0].times_s[-1] == times[59]


# ---------------------------------------------------------------- noise


def test_rejects_noise() -> None:
    rng = np.random.default_rng(SEED)
    times = frame_times(180)
    index = np.repeat(np.arange(180), 8)
    xy = rng.uniform((0.0, 0.0), (1920.0, 1080.0), (len(index), 2))
    assert link_flights(records(index, times, xy), times, constant_scale) == ()


def test_finds_the_ball_among_noise() -> None:
    rng = np.random.default_rng(SEED + 1)
    times = frame_times(90)
    ball = parabola(times, (300.0, 700.0), (900.0, -650.0))
    index = np.repeat(np.arange(90), 6)
    noise = records(index, times, rng.uniform((0.0, 0.0), (1920.0, 1080.0), (len(index), 2)))
    flights = link_flights(merged(noise, records(np.arange(90), times, ball)), times, constant_scale)
    assert len(flights) == 1
    assert int(flights[0].detected.sum()) >= 85
    assert np.allclose(flights[0].xy_px[flights[0].detected], ball[flights[0].detected], atol=1e-9)


# --------------------------------------------------------- two balls


def test_two_simultaneous_crossing_balls_stay_separate() -> None:
    times = frame_times(80)
    rng = np.random.default_rng(SEED)
    one = parabola(times, (200.0, 500.0), (900.0, -300.0)) + rng.normal(0.0, 0.5, (80, 2))
    two = parabola(times, (1000.0, 300.0), (-700.0, 100.0), gravity_px=300.0) + rng.normal(0.0, 0.5, (80, 2))
    frames = np.arange(80)
    flights = link_flights(merged(records(frames, times, one), records(frames, times, two)), times, constant_scale)
    assert len(flights) == 2
    for flight in flights:
        found = flight.xy_px[flight.detected]
        on_one = np.allclose(found, one[flight.detected])
        on_two = np.allclose(found, two[flight.detected])
        assert on_one != on_two
        assert int(flight.detected.sum()) >= 76


# ------------------------------------------------------------ determinism


def test_same_input_gives_identical_output() -> None:
    scene = generate_rally(SEED)
    camera = default_camera((1920, 1080))
    candidates, times, _ = t0_records(scene, camera, SEED)
    scale = scale_for(camera)
    first = link_flights(candidates, times, scale)
    second = link_flights(candidates.copy(), times.copy(), scale)
    assert len(first) == len(second) > 0
    for a, b in zip(first, second, strict=True):
        assert a.flight_id == b.flight_id and a.confidence == b.confidence
        for name in ("times_s", "xy_px", "detected", "minor_axis_px"):
            assert np.array_equal(getattr(a, name), getattr(b, name), equal_nan=name != "detected")


# --------------------------------------------------------- reject rules


@pytest.mark.parametrize(("fps", "count", "kept"), [(60.0, 5, False), (60.0, 6, True), (30.0, 3, False), (30.0, 4, True)])
def test_minimum_detections(fps: float, count: int, kept: bool) -> None:
    times = frame_times(count, fps)
    xy = np.column_stack([300.0 + 20.0 * np.arange(count), np.full(count, 400.0)])
    assert len(link_flights(records(np.arange(count), times, xy), times, constant_scale)) == int(kept)


def test_net_displacement_under_30_px_is_rejected() -> None:
    times = frame_times(12)
    short = np.column_stack([300.0 + 2.5 * np.arange(12), np.full(12, 400.0)])  # 27.5 px
    long = np.column_stack([300.0 + 3.0 * np.arange(12), np.full(12, 400.0)])  # 33 px
    assert link_flights(records(np.arange(12), times, short), times, constant_scale) == ()
    assert len(link_flights(records(np.arange(12), times, long), times, constant_scale)) == 1


def test_zigzag_is_rejected_as_not_a_ball() -> None:
    times = frame_times(40)
    xy = parabola(times, (300.0, 600.0), (700.0, -400.0))
    xy[:, 1] += np.where(np.arange(40) % 2 == 0, 6.0, -6.0)
    assert link_flights(records(np.arange(40), times, xy), times, constant_scale) == ()


def test_projected_parabola_fit_is_exact_where_a_quadratic_is_not() -> None:
    """A drag-free ball leaving the near-camera player: depth 4.7 -> 9.5 m in
    0.25 s. Its image path is not quadratic in time; its projection is."""
    camera = default_camera((1920, 1080))
    t = np.arange(16) / FPS
    xyz = np.column_stack([-1.0 + 0.5 * t, -13.6 + 31.0 * t, 1.2 + 4.0 * t - 4.905 * t * t])
    uv = camera.project(xyz)
    assert flight_track._quadratic_rms(t, uv) > 3.0
    assert flight_track._projected_parabola_rms(t, uv) < 1e-6
    assert flight_track._smooth_rms(t, uv) < 1e-6
    rng = np.random.default_rng(SEED)
    noise = rng.uniform(0.0, 300.0, (16, 2))
    assert flight_track._smooth_rms(t, noise) > 3.0


def test_kink_explains_a_v_but_not_two_bad_ends() -> None:
    t = np.arange(15) / FPS
    v_shape = np.column_stack([6.0 * np.arange(15), np.abs(np.arange(15) - 7) * 9.0])
    assert flight_track._quadratic_rms(t, v_shape) > 3.0
    assert flight_track._kink_explains(t, v_shape)
    jagged = np.column_stack([6.0 * np.arange(15), np.zeros(15)])
    jagged[[0, 2, 12, 14], 1] = (12.0, -12.0, 12.0, -12.0)
    assert not flight_track._kink_explains(t, jagged)


def test_streak_must_lie_along_its_step() -> None:
    times = frame_times(20)
    xy = np.column_stack([300.0 + 15.0 * np.arange(20), np.full(20, 400.0)])
    along = records(np.arange(20), times, xy, minor=4.0, major=14.0, orientation=0.0)
    across = records(np.arange(20), times, xy, minor=4.0, major=14.0, orientation=90.0)
    assert len(link_flights(along, times, constant_scale)) == 1
    assert link_flights(across, times, constant_scale) == ()


# ---------------------------------------------------------- validation


def test_invalid_inputs_raise() -> None:
    times = frame_times(10)
    good = records(np.arange(10), times, np.zeros((10, 2)))
    with pytest.raises(ValueError):
        link_flights(np.zeros(3, dtype=np.float64), times, constant_scale)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        link_flights(good, times[:5], constant_scale)
    with pytest.raises(ValueError):
        link_flights(good, times[::-1].copy(), constant_scale)
    moving = records(np.arange(10), times, np.column_stack([10.0 * np.arange(10), np.zeros(10)]))
    with pytest.raises(ValueError):
        link_flights(moving, times, lambda x, y: math.nan)


def test_empty_input_gives_no_flights() -> None:
    times = frame_times(10)
    assert link_flights(np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE), times, constant_scale) == ()
