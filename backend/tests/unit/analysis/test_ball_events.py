"""Build step 10: ``analysis/ball_events.py`` (design 5.2-5.4).

Done when: bounce vs hit vs net on T0, each with a synthetic case; a
near-side hit without pose is not called a bounce; an in-flight point never
yields a ground point; pose hits are optional.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import numpy.typing as npt
import pytest

from app.analysis import ball_events
from app.analysis.ball_events import (
    FLIGHT_START_FLAG,
    GRAMMAR_RELABELLED_FLAG,
    ONE_SIDED_BOUNCE_FLAG,
    POSE_HIT_FLAG,
    WEAK_BOUNCE_FLAG,
    BallEvent,
    PoseHit,
    classify_event,
    detect_ball_events,
    find_break_points,
    resolve_event_grammar,
)
from app.analysis.court_calibration import CourtCalibration, CourtTap, calibrate_court
from app.analysis.court_model import CourtEnd, CourtKeypoint, keypoint_xy
from app.analysis.flight_track import BallFlight, link_flights
from app.analysis.homography import image_scale_px_per_m
from app.models.court import BallEventKind
from tests.unit.analysis.test_flight_track import t0_records
from tests.unit.court_synth.camera import SynthCamera, default_camera
from tests.unit.court_synth.physics import FlightEventKind, Trajectory, simulate
from tests.unit.court_synth.scene import SceneTruth, generate_rally

FPS = 60.0
SEEDS = range(20261102, 20261110)
MATCH_S = 2.0 / FPS  # design 11.2: +/- 2 frames


def calibration_for(camera: SynthCamera) -> CourtCalibration:
    keypoints = list(CourtKeypoint)
    uv = camera.project(np.array([[*keypoint_xy(k), 0.0] for k in keypoints]))
    taps = [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(keypoints, uv, strict=True)]
    calibration = calibrate_court(taps, camera.frame_size).calibration
    assert calibration is not None
    return calibration


CAMERA = default_camera((1920, 1080))
CALIBRATION = calibration_for(CAMERA)


def scale(x: float, y: float) -> float:
    return image_scale_px_per_m(CALIBRATION.pose, CALIBRATION.h_image_to_court, (x, y))


def visible(truth_uv: npt.NDArray[np.float64], time_s: float) -> bool:
    frame = int(round(time_s * FPS))
    window = truth_uv[max(frame - 3, 0) : frame + 4]
    return frame < len(truth_uv) and bool(np.all(np.isfinite(window)))


def nearest(events: tuple[BallEvent, ...], time_s: float) -> BallEvent | None:
    close = [e for e in events if abs(e.time_s - time_s) <= MATCH_S]
    return min(close, key=lambda e: abs(e.time_s - time_s)) if close else None


def scene_events(seed: int, pose_hits: tuple[PoseHit, ...] = ()) -> tuple[SceneTruth, tuple[BallEvent, ...], npt.NDArray[np.float64]]:
    scene = generate_rally(seed)
    candidates, times, truth_uv = t0_records(scene, CAMERA, seed)
    flights = link_flights(candidates, times, scale)
    return scene, detect_ball_events(flights, CALIBRATION, pose_hits), truth_uv


def flight_from(trajectory: Trajectory, start_s: float, end_s: float, seed: int, noise_px: float = 0.5) -> BallFlight:
    times = np.arange(math.ceil(start_s * FPS), math.floor(end_s * FPS) + 1) / FPS
    uv = CAMERA.project(trajectory.position_at(times))
    uv += np.random.default_rng(seed).normal(0.0, noise_px, uv.shape)
    return BallFlight(0, times, uv, np.ones(len(times), dtype=bool), np.full(len(times), 2.0), 1.0)


# ------------------------------------------------------------ T0: bounces


def test_t0_bounces_are_found_and_called_bounces() -> None:
    truth_count = found = wrong_kind = 0
    bounce_events = matched_bounce_events = 0
    for seed in SEEDS:
        scene, events, truth_uv = scene_events(seed)
        truths = [b.time_s for b in scene.bounces if visible(truth_uv, b.time_s)]
        for time_s in truths:
            truth_count += 1
            event = nearest(events, time_s)
            if event is not None and event.kind is BallEventKind.BOUNCE:
                found += 1
            elif event is not None and event.kind is BallEventKind.HIT:
                wrong_kind += 1
        for event in events:
            if event.kind is BallEventKind.BOUNCE:
                bounce_events += 1
                matched_bounce_events += any(abs(event.time_s - t) <= MATCH_S for t in truths)
    assert truth_count >= 35
    assert found / truth_count >= 0.9
    assert wrong_kind <= 1
    assert matched_bounce_events / bounce_events >= 0.95


def test_t0_near_side_hits_without_pose_are_never_bounces() -> None:
    checked = 0
    for seed in SEEDS:
        scene, events, truth_uv = scene_events(seed)
        for rally in scene.rallies:
            for hit in rally.hits[1:]:
                if hit.end is not CourtEnd.NEAR or not visible(truth_uv, hit.time_s):
                    continue
                event = nearest(events, hit.time_s)
                assert event is not None, (seed, hit.time_s)
                checked += 1
                assert event.kind is BallEventKind.HIT, (seed, hit.time_s, event.kind)
    assert checked >= 6


def test_t0_hit_ends_come_from_the_bounces_around_them() -> None:
    right = total = 0
    for seed in SEEDS:
        scene, events, truth_uv = scene_events(seed)
        for rally in scene.rallies:
            for hit in rally.hits[1:]:
                event = nearest(events, hit.time_s)
                if event is not None and event.kind is BallEventKind.HIT and event.end is not None:
                    total += 1
                    right += event.end is hit.end
    assert total >= 15
    assert right == total


# --------------------------------------------------------- net, synthetic


def net_trajectory() -> Trajectory:
    """Hit from the near baseline into the net at about half its height; the
    ball drops dead on the hitter's side."""
    trajectory = simulate(np.array([0.5, -11.9, 1.0]), np.array([0.0, 20.0, 2.6]), 1.0, 2.0)
    assert any(e.kind is FlightEventKind.NET for e in trajectory.events)
    return trajectory


def test_net_contact_mid_net_is_net_then_the_drop_is_a_bounce() -> None:
    trajectory = net_trajectory()
    net = next(e for e in trajectory.events if e.kind is FlightEventKind.NET)
    bounce = next(e for e in trajectory.events if e.kind is FlightEventKind.BOUNCE)
    assert 0.3 < net.position_m[2] < 0.8 and bounce.time_s - net.time_s > 0.08
    flight = flight_from(trajectory, 1.0, bounce.time_s + 0.3, seed=5)
    events = detect_ball_events([flight], CALIBRATION)
    at_net = nearest(events, net.time_s)
    assert at_net is not None and at_net.kind is BallEventKind.NET_CONTACT
    at_bounce = nearest(events, bounce.time_s)
    assert at_bounce is not None and at_bounce.kind is BallEventKind.BOUNCE


# ----------------------------------------------------------- pose is optional


def test_pose_hits_are_optional_and_mark_hits_and_the_serve() -> None:
    seed = SEEDS[0]
    scene, without, truth_uv = scene_events(seed)
    rally = scene.rallies[0]
    pose = tuple(PoseHit(h.time_s, None, h.end, 0.9, "serve" if h.is_serve else "forehand") for h in rally.hits)
    _, with_pose, _ = scene_events(seed, pose)
    serve = nearest(with_pose, rally.hits[0].time_s)
    assert serve is not None and serve.kind is BallEventKind.HIT
    assert FLIGHT_START_FLAG in serve.flags and POSE_HIT_FLAG in serve.flags
    assert serve.end is rally.hits[0].end and serve.shot_type == "serve"
    for hit in rally.hits[1:]:
        if not visible(truth_uv, hit.time_s):
            continue
        event = nearest(with_pose, hit.time_s)
        if event is not None:
            assert event.kind is BallEventKind.HIT and event.end is hit.end and POSE_HIT_FLAG in event.flags
    bounces_without = [e.time_s for e in without if e.kind is BallEventKind.BOUNCE]
    bounces_with = [e.time_s for e in with_pose if e.kind is BallEventKind.BOUNCE]
    assert bounces_without == bounces_with
    assert not any(POSE_HIT_FLAG in e.flags for e in without)


def test_pose_hit_outside_the_window_or_far_from_the_wrist_is_ignored() -> None:
    event = BallEvent("e", 0, BallEventKind.UNKNOWN_KINK, 2.0, 0.003, (900.0, 700.0), np.eye(2),
                      (0.0, 100.0), (0.0, -100.0), 0.9, ())
    late = PoseHit(2.0 + 0.06, None, CourtEnd.NEAR, 0.9)
    far_wrist = PoseHit(2.0, (100.0, 100.0), CourtEnd.NEAR, 0.9)
    near_wrist = PoseHit(2.01, (905.0, 690.0), CourtEnd.NEAR, 0.9)
    assert POSE_HIT_FLAG not in classify_event(event, CALIBRATION, [late, far_wrist]).flags
    matched = classify_event(event, CALIBRATION, [near_wrist])
    assert matched.kind is BallEventKind.HIT and matched.end is CourtEnd.NEAR


def test_hitter_ground_point_maps_the_feet_and_nothing_else() -> None:
    from app.analysis.ball_events import hitter_ground_point

    behind_baseline = (1.2, -13.5)
    u, v = CAMERA.project(np.array([[*behind_baseline, 0.0]]))[0]
    ground = hitter_ground_point(CALIBRATION, (float(u), float(v)))
    assert ground is not None
    assert math.dist(ground, behind_baseline) < 0.01
    assert hitter_ground_point(CALIBRATION, None) is None
    # In the stands: mapped by the homography, but not a place a hitter stands.
    u, v = CAMERA.project(np.array([[14.0, 0.0, 0.0]]))[0]
    assert hitter_ground_point(CALIBRATION, (float(u), float(v))) is None


def test_a_pose_hit_passes_its_feet_on_in_pixels() -> None:
    event = BallEvent("e", 0, BallEventKind.UNKNOWN_KINK, 2.0, 0.003, (900.0, 700.0), np.eye(2),
                      (0.0, 100.0), (0.0, -100.0), 0.9, ())
    hit = PoseHit(2.0, None, CourtEnd.NEAR, 0.9, "forehand", feet_xy_px=(880.0, 760.0))
    assert classify_event(event, CALIBRATION, [hit]).hitter_feet_xy_px == (880.0, 760.0)
    assert classify_event(event, CALIBRATION, [PoseHit(2.0, None, CourtEnd.NEAR, 0.9)]).hitter_feet_xy_px is None


# ------------------------------------------- no ground point for a flight


def test_no_event_carries_a_court_position() -> None:
    names = {f.name for f in dataclasses.fields(BallEvent)}
    assert not any("court" in name or name.endswith("_m") for name in names)
    _, events, _ = scene_events(SEEDS[0])
    assert {e.kind for e in events} >= {BallEventKind.BOUNCE, BallEventKind.HIT}


# ------------------------------------------------------------ break points


def image_v(count_before: int, count_after: int) -> BallFlight:
    times = np.arange(count_before + count_after) / FPS
    steps = np.array([(6.0, 10.0)] * count_before + [(6.0, -7.0)] * (count_after - 1))
    xy = np.vstack([[(300.0, 200.0)], (300.0, 200.0) + np.cumsum(steps, axis=0)])
    return BallFlight(3, times, xy, np.ones(len(times), dtype=bool), np.full(len(times), 2.0), 1.0)


def test_a_v_gives_one_break_at_its_vertex() -> None:
    flight = image_v(20, 20)
    (event,) = find_break_points(flight)
    assert event.event_id == "tmp_3_0" and event.kind is BallEventKind.UNKNOWN_KINK
    assert event.time_s == pytest.approx(flight.times_s[20], abs=1.0 / FPS)  # the vertex
    assert event.velocity_before_px_s == pytest.approx((360.0, 600.0), rel=1e-6)
    assert event.velocity_after_px_s == pytest.approx((360.0, -420.0), rel=1e-6)
    assert event.confidence == pytest.approx(1.0)
    assert ONE_SIDED_BOUNCE_FLAG not in event.flags
    assert 0.0 < event.time_sigma_s <= (1.0 / FPS) / math.sqrt(12.0) + 1e-12
    assert np.allclose(event.image_cov_px2, event.image_cov_px2.T)
    assert np.all(np.linalg.eigvalsh(event.image_cov_px2) > 0.0)


def test_smooth_flights_and_noise_give_no_breaks() -> None:
    times = np.arange(60) / FPS
    rng = np.random.default_rng(3)
    xy = np.column_stack([300.0 + 400.0 * times, 600.0 - 500.0 * times + 300.0 * times**2])
    noisy = xy + rng.normal(0.0, 0.5, xy.shape)
    flight = BallFlight(0, times, noisy, np.ones(60, dtype=bool), np.full(60, 2.0), 1.0)
    assert find_break_points(flight) == ()


def test_two_samples_after_a_break_is_one_sided() -> None:
    flight = image_v(20, 2)  # the vertex, then two samples
    (event,) = find_break_points(flight)
    assert ONE_SIDED_BOUNCE_FLAG in event.flags
    assert event.time_sigma_s == pytest.approx((1.0 / FPS) / math.sqrt(12.0))
    assert event.confidence == pytest.approx(2.0 / 8.0, abs=1e-9)


def test_coasted_samples_are_not_used() -> None:
    flight = image_v(20, 20)
    detected = flight.detected.copy()
    detected[[5, 6, 30]] = False
    xy = flight.xy_px.copy()
    xy[~detected] = np.nan
    gappy = dataclasses.replace(flight, xy_px=xy, detected=detected)
    (event,) = find_break_points(gappy)
    assert event.time_s == pytest.approx(flight.times_s[20], abs=1.0 / FPS)


# --------------------------------------------------- classify, synthetic


def velocities_of(trajectory: Trajectory, time_s: float) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    def at(t: float) -> npt.NDArray[np.float64]:
        return CAMERA.project(trajectory.position_at([t]))[0]
    point = at(time_s)
    before = (at(time_s - 0.002) - at(time_s - 0.004)) / 0.002
    after = (at(time_s + 0.004) - at(time_s + 0.002)) / 0.002
    return (float(point[0]), float(point[1])), (float(before[0]), float(before[1])), (float(after[0]), float(after[1]))


def synthetic_event(point: tuple[float, float], before: tuple[float, float], after: tuple[float, float],
                    confidence: float = 0.9) -> BallEvent:
    return BallEvent("tmp", 0, BallEventKind.UNKNOWN_KINK, 2.0, 0.003, point, np.eye(2), before, after,
                     confidence, ())


@pytest.mark.parametrize("y_target", [-8.0, 9.0])
def test_classify_true_bounce_velocities_as_bounce(y_target: float) -> None:
    start_y = 11.9 if y_target < 0 else -11.9
    speed = -18.0 if y_target < 0 else 18.0
    trajectory = simulate(np.array([1.0, start_y, 1.0]), np.array([0.0, speed, 4.0]), 0.0, 3.0, collide_net=False)
    bounce = next(e for e in trajectory.events if e.kind is FlightEventKind.BOUNCE)
    point, before, after = velocities_of(trajectory, bounce.time_s)
    event = classify_event(synthetic_event(point, before, after), CALIBRATION)
    assert event.kind is BallEventKind.BOUNCE
    weak = classify_event(synthetic_event(point, before, after, confidence=0.3), CALIBRATION)
    assert weak.kind is BallEventKind.BOUNCE and WEAK_BOUNCE_FLAG in weak.flags


def test_classify_a_near_hit_as_hit_not_bounce() -> None:
    """Incoming toward the near player, struck at about 1 m back up the court."""
    incoming = simulate(np.array([0.5, -2.0, 2.2]), np.array([0.3, -14.0, 1.0]), 0.0, 0.7, collide_net=False,
                        stop_after_bounces=0)
    contact_s = 0.6
    position = incoming.position_at([contact_s])[0]
    outgoing = simulate(position, np.array([-0.5, 25.0, 4.0]), contact_s, 0.3, collide_net=False)
    point = CAMERA.project(position[None])[0]
    before = (point - CAMERA.project(incoming.position_at([contact_s - 0.002]))[0]) / 0.002
    after = (CAMERA.project(outgoing.position_at([contact_s + 0.002]))[0] - point) / 0.002
    event = classify_event(
        synthetic_event((float(point[0]), float(point[1])), tuple(before), tuple(after)), CALIBRATION
    )
    assert event.kind is BallEventKind.HIT


def test_classify_a_gentle_turn_as_unknown() -> None:
    event = classify_event(synthetic_event((960.0, 800.0), (100.0, 50.0), (90.0, 60.0)), CALIBRATION)
    assert event.kind is BallEventKind.UNKNOWN_KINK


# ----------------------------------------------------------------- grammar


def grammar_event(name: str, time_s: float, kind: BallEventKind, confidence: float,
                  flags: tuple[str, ...] = (), point: tuple[float, float] = (960.0, 500.0)) -> BallEvent:
    return BallEvent(name, 0, kind, time_s, 0.003, point, np.eye(2), (0.0, 100.0), (0.0, -100.0), confidence, flags)


def test_grammar_relabels_a_weak_second_hit_as_a_bounce() -> None:
    events = [
        grammar_event("a", 1.0, BallEventKind.HIT, 0.9),
        grammar_event("b", 1.5, BallEventKind.HIT, 0.3),
        grammar_event("c", 2.0, BallEventKind.HIT, 0.9),
    ]
    resolved = resolve_event_grammar(events, CALIBRATION)
    assert [e.kind for e in resolved] == [BallEventKind.HIT, BallEventKind.BOUNCE, BallEventKind.HIT]
    assert GRAMMAR_RELABELLED_FLAG in resolved[1].flags and WEAK_BOUNCE_FLAG in resolved[1].flags
    assert len(resolve_event_grammar(events[:1], CALIBRATION)) == 1


def test_grammar_leaves_confident_and_pose_events_alone() -> None:
    events = [
        grammar_event("a", 1.0, BallEventKind.HIT, 0.9),
        grammar_event("b", 1.5, BallEventKind.HIT, 0.7),
        grammar_event("c", 2.0, BallEventKind.HIT, 0.2, (POSE_HIT_FLAG,)),
        grammar_event("d", 2.5, BallEventKind.UNKNOWN_KINK, 0.1),
    ]
    resolved = resolve_event_grammar(events, CALIBRATION)
    assert [e.kind for e in resolved] == [e.kind for e in events]
    assert resolve_event_grammar([], CALIBRATION) == ()


# ------------------------------------------------------------- ids, order


def test_final_ids_are_one_running_index_in_time_order() -> None:
    _, events, _ = scene_events(SEEDS[1])
    assert [e.time_s for e in events] == sorted(e.time_s for e in events)
    prefixes = {BallEventKind.BOUNCE: "b", BallEventKind.HIT: "h", BallEventKind.NET_CONTACT: "n",
                BallEventKind.UNKNOWN_KINK: "k"}
    for index, event in enumerate(events):
        assert event.event_id == f"{prefixes[event.kind]}_{index:06d}"


def test_same_input_gives_identical_events() -> None:
    _, first, _ = scene_events(SEEDS[2])
    _, second, _ = scene_events(SEEDS[2])
    assert len(first) == len(second) > 0
    for a, b in zip(first, second, strict=True):
        assert (a.event_id, a.kind, a.time_s, a.image_xy_px, a.end, a.flags) == (
            b.event_id, b.kind, b.time_s, b.image_xy_px, b.end, b.flags)
        assert np.array_equal(a.image_cov_px2, b.image_cov_px2)


def test_version_constant() -> None:
    assert ball_events.BALL_EVENTS_VERSION == "ball_events_v1"
