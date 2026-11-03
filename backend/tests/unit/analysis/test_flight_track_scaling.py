"""Linker scaling and clutter (fix to build step 9, before step 10).

Real handheld clips gave 80-2500 candidates per frame, and the first linker's
seeding was cubic in that count. These tests pin the fix: the per-frame cap is
deterministic, linking a T0 scene inside 500 random candidates per frame
finishes inside a generous wall-clock bound, and the true flight is still
recovered with the serve bounce inside it.

The clutter is honest about what the cap can do: half of it carries the same
colour and contrast distribution as the ball (the cap cannot tell those apart,
so the linker must), half carries lower scores (H.264 flicker, a player's
shirt edge), which the cap is allowed to drop.
"""

from __future__ import annotations

import time

import numpy as np
import numpy.typing as npt
import pytest

from app.analysis import flight_track
from app.analysis.flight_track import (
    FLIGHT_CANDIDATE_DTYPE,
    MAX_CANDIDATES_PER_FRAME,
    cap_candidates_per_frame,
    link_flights,
)
from tests.unit.analysis.test_flight_track import (
    constant_scale,
    flight_of,
    frame_times,
    parabola,
    records,
    scale_for,
    t0_records,
)
from tests.unit.court_synth.camera import default_camera
from tests.unit.court_synth.scene import generate_rally

FPS = 60.0
WINDOW_FRAMES = 150
CLUTTER_PER_FRAME = 500
#: Measured about 3.3 s on the build machine; the first linker needed far
#: longer (see the commit message). Generous on purpose: this is a cost bound,
#: not a benchmark.
WALL_CLOCK_LIMIT_S = 60.0


def scored(rows: npt.NDArray[np.void], rng: np.random.Generator, colour: tuple[float, float],
           contrast: tuple[float, float]) -> npt.NDArray[np.void]:
    out = rows.copy()
    out["colour_score"] = rng.uniform(*colour, len(out))
    out["contrast"] = rng.uniform(*contrast, len(out))
    return out


def uniform_clutter(per_frame: int, times: npt.NDArray[np.float64], rng: np.random.Generator,
                    size: tuple[int, int] = (1920, 1080)) -> npt.NDArray[np.void]:
    index = np.repeat(np.arange(len(times)), per_frame)
    xy = rng.uniform((0.0, 0.0), size, (len(index), 2))
    return records(index, times, xy)


# ------------------------------------------------------------------ the cap


def test_cap_keeps_highest_colour_then_contrast_then_lowest_index() -> None:
    times = frame_times(2)
    rows = records([0, 0, 0, 0, 1], times, np.zeros((5, 2)))
    rows["colour_score"] = [0.5, 0.9, 0.5, 0.5, 0.0]
    rows["contrast"] = [10.0, 1.0, 30.0, 10.0, 0.0]
    assert cap_candidates_per_frame(rows, cap=3).tolist() == [0, 1, 2, 4]
    assert cap_candidates_per_frame(rows, cap=1).tolist() == [1, 4]


def test_cap_leaves_small_frames_alone_and_ranks_nan_last() -> None:
    times = frame_times(3)
    rows = records([2, 0, 0, 1], times, np.zeros((4, 2)))
    assert cap_candidates_per_frame(rows).tolist() == [0, 1, 2, 3]
    rows["colour_score"] = [0.0, np.nan, 0.1, 0.0]
    assert cap_candidates_per_frame(rows, cap=1).tolist() == [0, 2, 3]
    assert cap_candidates_per_frame(rows[:0]).size == 0
    with pytest.raises(ValueError):
        cap_candidates_per_frame(rows, cap=0)


def test_link_flights_applies_the_cap() -> None:
    """Over the cap, a ball scored above the clutter is kept and linked."""
    rng = np.random.default_rng(7)
    times = frame_times(40)
    ball = records(np.arange(40), times, parabola(times, (300.0, 700.0), (900.0, -650.0)))
    ball["colour_score"] = 0.9
    clutter = uniform_clutter(MAX_CANDIDATES_PER_FRAME + 20, times, rng)
    joined = np.concatenate([clutter, ball])
    flights = link_flights(joined[np.argsort(joined["frame_index"], kind="stable")], times, constant_scale)
    assert any(int(f.detected.sum()) >= 36 for f in flights)


# ------------------------------------------------- cost bound with clutter


@pytest.mark.parametrize("seed", [20261102, 20261105])
def test_t0_scene_in_500_clutter_per_frame_is_fast_and_recovered(seed: int) -> None:
    scene = generate_rally(seed)
    camera = default_camera((1920, 1080))
    scale = scale_for(camera)
    candidates, all_times, truth_uv = t0_records(scene, camera, seed)
    rally = scene.rallies[0]
    first = int(round(rally.hits[0].time_s * FPS)) - 3
    times = all_times[first : first + WINDOW_FRAMES].copy()
    truth = truth_uv[first : first + WINDOW_FRAMES]
    ball = candidates[(candidates["frame_index"] >= first) & (candidates["frame_index"] < first + WINDOW_FRAMES)].copy()
    ball["frame_index"] -= first

    rng = np.random.default_rng(seed + 99)
    half = CLUTTER_PER_FRAME // 2
    ball_like = scored(uniform_clutter(half, times, rng), rng, (0.3, 1.0), (20.0, 60.0))
    flicker = scored(uniform_clutter(half, times, rng), rng, (0.0, 0.25), (8.0, 60.0))
    ball = scored(ball, rng, (0.3, 1.0), (20.0, 60.0))
    # Clutter first, so equal-score ties can never favour the ball's records.
    joined = np.concatenate([ball_like, flicker, ball])
    joined = joined[np.argsort(joined["frame_index"], kind="stable")]
    assert len(joined) >= CLUTTER_PER_FRAME * WINDOW_FRAMES

    started = time.perf_counter()
    flights = link_flights(joined, times, scale)
    elapsed = time.perf_counter() - started
    assert elapsed < WALL_CLOCK_LIMIT_S

    def on_truth(flight: flight_track.BallFlight) -> npt.NDArray[np.bool_]:
        frames = np.round((flight.times_s[flight.detected] - times[0]) * FPS).astype(int)
        error = np.hypot(*(flight.xy_px[flight.detected] - truth[frames]).T)
        return np.asarray(error < 3.0)

    main = max(flights, key=lambda f: int(np.sum(on_truth(f))))
    good = on_truth(main)
    assert int(good.sum()) >= 0.85 * len(ball)
    assert int((~good).sum()) <= 3
    bounce_s = rally.bounces[0].time_s
    found = main.times_s[main.detected][good]
    assert np.any((found < bounce_s) & (found >= bounce_s - 3.0 / FPS))
    assert np.any((found > bounce_s) & (found <= bounce_s + 3.0 / FPS))


# ---------------------------------------------------------- the new rules


def test_probation_refuses_an_impossible_acceleration() -> None:
    """At 60 px/m the limit is 600 x 60 + 3 x 60^2 = 46,800 px/s^2."""
    t = np.arange(6) / FPS
    possible = [(float(s), 300.0 + 20.0 * k + 0.5 * 40_000.0 * s * s, 400.0) for k, s in enumerate(t)]
    impossible = [(float(s), 300.0 + 20.0 * k + 0.5 * 60_000.0 * s * s, 400.0) for k, s in enumerate(t)]
    assert flight_track._physically_plausible(possible, constant_scale, 1.0 / FPS)
    assert not flight_track._physically_plausible(impossible, constant_scale, 1.0 / FPS)


def test_a_failed_stitch_is_undone_and_its_pieces_judged_alone() -> None:
    times = frame_times(80)
    path = parabola(times, (100.0, 500.0), (600.0, -300.0))
    head = path[0:32].copy()
    head[30:32] += (0.0, 60.0)  # two stray points the head ends on
    a = flight_of(times, 0, head, 0)
    b = flight_of(times, 35, path[35:70], 1)
    chain = flight_track._stitch_chains([a, b], times, constant_scale)
    assert chain == [[0, 1]]
    assert not flight_track._accept(flight_track._join(chain[0], [a, b], times), 6)
    kept = flight_track._accepted_flights(chain, [a, b], times, 6)
    assert [f.flight_id for f in kept] == [0, 1]


def test_a_flight_ending_in_clutter_does_not_carry_it() -> None:
    """The ball stops being detected; a verified track must coast out rather
    than walk on through random candidates."""
    rng = np.random.default_rng(11)
    times = frame_times(80)
    path = parabola(times, (300.0, 700.0), (900.0, -650.0))
    ball = records(np.arange(40), times, path[:40])
    clutter = uniform_clutter(40, times, rng)
    joined = np.concatenate([clutter, ball])
    flights = link_flights(joined[np.argsort(joined["frame_index"], kind="stable")], times, constant_scale)
    longest = max(flights, key=lambda f: int(f.detected.sum()))
    frames = np.round(longest.times_s[longest.detected] * FPS).astype(int)
    assert frames[-1] == 39
    assert int(longest.detected.sum()) >= 38
    assert np.allclose(longest.xy_px[longest.detected], path[frames], atol=1e-9)
