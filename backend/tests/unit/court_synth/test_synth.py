"""Build step 6: the synthetic court renderer itself.

Done criteria (design 12): same seed gives byte-identical frames; ball falls
with correct g; bounce energy matches e_z; projection round-trip correct; no
file written. Renders here are small (640x360) so this stays in BASE; the
full-size T1/T2 evaluations carry the ``court_pixels`` marker in step 17.
"""

from __future__ import annotations

import builtins
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from app.analysis.court_model import (
    CourtEnd,
    CourtLine,
    court_line_endpoints,
    half_region,
)
from app.analysis.homography import apply_homography, invert_homography
from app.models.court import CourtFormat, LineCallTarget, RallyEndReason
from tests.unit.court_synth import physics, truth
from tests.unit.court_synth.camera import default_camera, focal_for_width, look_at
from tests.unit.court_synth.physics import (
    BALL_RADIUS_M,
    GRAVITY_M_S2,
    HORIZONTAL_RETENTION,
    RESTITUTION_Z,
    FlightEventKind,
    simulate,
)
from tests.unit.court_synth.render import CourtRenderer, RenderOptions, Surface, frame_times_s
from tests.unit.court_synth.scene import BallRole, Scenario, generate_match, generate_rally

SEED = 20261102
SMALL = (640, 360)


# ----------------------------------------------------------------------- physics


def test_ball_falls_with_correct_g() -> None:
    """Drag off, RK4 is exact for a quadratic: z = z0 - g t^2 / 2."""
    trajectory = simulate((0.0, 3.0, 5.0), (0.0, 0.0, 0.0), 0.0, 1.2, drag_k=0.0)
    z = trajectory.positions_m[:, 2]
    t = trajectory.times_s
    before = t < math.sqrt(2.0 * (5.0 - BALL_RADIUS_M) / GRAVITY_M_S2)
    assert np.allclose(z[before], 5.0 - 0.5 * GRAVITY_M_S2 * t[before] ** 2, atol=1e-9)
    bounce = trajectory.events[0]
    assert bounce.kind is FlightEventKind.BOUNCE
    assert bounce.time_s == pytest.approx(
        math.sqrt(2.0 * (5.0 - BALL_RADIUS_M) / GRAVITY_M_S2), abs=1e-9
    )


def test_drag_is_quadratic_with_k_0_0204() -> None:
    assert physics.DRAG_K_PER_M == 0.0204
    velocity = (30.0, 0.0, 0.0)
    ax, _, az = physics.acceleration(velocity, physics.DRAG_K_PER_M)
    assert ax == pytest.approx(-0.0204 * 30.0 * 30.0)
    assert az == pytest.approx(-GRAVITY_M_S2)
    fast = simulate((0.0, -12.0, 1.0), (0.0, 30.0, 0.0), 0.0, 0.5, collide_net=False)
    assert fast.positions_m[-1, 1] < -12.0 + 30.0 * 0.5  # drag slowed it


def test_bounce_energy_matches_e_z() -> None:
    trajectory = simulate((0.0, 3.0, 2.0), (4.0, 0.0, 0.0), 0.0, 1.5, drag_k=0.0)
    bounce = next(e for e in trajectory.events if e.kind is FlightEventKind.BOUNCE)
    assert bounce.velocity_after_m_s[2] == pytest.approx(-RESTITUTION_Z * bounce.velocity_before_m_s[2])
    assert bounce.velocity_after_m_s[0] == pytest.approx(
        HORIZONTAL_RETENTION * bounce.velocity_before_m_s[0]
    )
    # Vertical kinetic energy after / before = e_z^2, so the apex height is too.
    after = trajectory.times_s > bounce.time_s
    rise = float(np.max(trajectory.positions_m[after, 2])) - BALL_RADIUS_M
    fall = 2.0 - BALL_RADIUS_M
    assert rise / fall == pytest.approx(RESTITUTION_Z**2, rel=1e-3)


def test_slow_rebound_comes_to_rest_and_rolls() -> None:
    trajectory = simulate((0.0, 3.0, 0.2), (1.0, 0.0, 0.0), 0.0, 3.0, drag_k=0.0)
    assert trajectory.positions_m[-1, 2] == pytest.approx(BALL_RADIUS_M)
    assert len(trajectory.events) < 10  # no Zeno bounce storm


def test_net_stops_a_low_ball_and_passes_a_high_one() -> None:
    low = simulate((0.0, -8.0, 0.4), (0.0, 20.0, 1.0), 0.0, 1.5)
    net = [e for e in low.events if e.kind is FlightEventKind.NET]
    assert len(net) == 1
    assert net[0].position_m[1] == pytest.approx(-BALL_RADIUS_M)
    assert net[0].position_m[2] < physics.net_height_m(0.0) + BALL_RADIUS_M
    assert np.all(low.positions_m[low.times_s > net[0].time_s, 1] < 0.0)
    high = simulate((0.0, -8.0, 2.0), (0.0, 20.0, 2.0), 0.0, 1.5)
    assert not any(e.kind is FlightEventKind.NET for e in high.events)
    assert high.positions_m[-1, 1] > 0.0


def test_net_height_profile() -> None:
    assert physics.net_height_m(0.0) == pytest.approx(0.914)
    assert physics.net_height_m(6.399) == pytest.approx(1.07)
    assert physics.net_height_m(-3.0) == physics.net_height_m(3.0)


# ------------------------------------------------------------------------ camera


def test_projection_round_trip_is_exact() -> None:
    camera = default_camera((1920, 1080))
    court = np.array([[0.0, 11.0], [-4.0, -10.0], [3.9, 6.4], [-1.0, 0.5]])
    uv = camera.project(np.column_stack([court, np.zeros(len(court))]))
    assert np.allclose(apply_homography(camera.ground_homography(), court), uv, atol=1e-9)
    back = apply_homography(invert_homography(camera.ground_homography()), uv)
    assert np.allclose(back, court, atol=1e-9)


def test_default_camera_matches_the_design_placement() -> None:
    camera = default_camera((1280, 720))
    assert camera.focal_px == pytest.approx(1004.7, abs=0.1)  # design 2.2: ~1005 at 1280
    assert focal_for_width(1920) == pytest.approx(1507.1, abs=0.1)
    assert camera.position_m == (0.0, -17.885, 3.5)
    # The recommended six taps are on the frame.
    keypoints = np.array([[-4.115, -11.885, 0], [4.115, -11.885, 0], [-4.115, 11.885, 0],
                          [4.115, 11.885, 0], [0, -6.4, 0], [0, 6.4, 0]], dtype=float)
    uv = camera.project(keypoints)
    assert np.all((uv >= 0) & (uv <= np.array([1280, 720])))


def test_distortion_bends_points_but_not_the_ground_homography() -> None:
    plain = default_camera((1920, 1080))
    bent = plain.with_options(k1=-0.25)
    corner = np.array([[4.115, -11.885, 0.0]])
    assert np.allclose(plain.ground_homography(), bent.ground_homography())
    shift = np.linalg.norm(bent.project(corner) - plain.project(corner))
    assert shift > 20.0
    centre_ray = np.array([[0.0, -1.0, 0.0]])  # the look-at target sits on the axis
    assert np.allclose(bent.project(centre_ray), plain.project(centre_ray), atol=1e-6)


def test_rolling_shutter_delays_lower_rows() -> None:
    camera = default_camera((1920, 1080)).with_options(readout_s=0.02)
    assert camera.row_time_offset_s(0.0) == 0.0
    assert camera.row_time_offset_s(1080.0) == pytest.approx(0.02)

    def moving(t: float) -> np.ndarray:
        return np.array([[10.0 * t, 0.0, 1.0]])

    uv, time_s = camera.project_moving(moving, 0.0)
    assert time_s == pytest.approx(camera.row_time_offset_s(float(uv[1])), abs=1e-9)
    assert np.allclose(uv, camera.project(moving(time_s))[0], atol=1e-6)


def test_points_behind_the_camera_are_nan() -> None:
    camera = default_camera((1920, 1080))
    assert np.all(np.isnan(camera.project(np.array([[0.0, -30.0, 1.0]]))))


# ------------------------------------------------------------------------- scene


def _first_bounces(rally: Any) -> dict[int, Any]:
    return {b.shot_index: b for b in rally.bounces if b.bounce_index == 1}


@pytest.mark.parametrize("seed", [SEED, SEED + 1, SEED + 2])
def test_clean_rally_truth(seed: int) -> None:
    scene = generate_rally(seed, Scenario.CLEAN_RALLY)
    (rally,) = scene.rallies
    firsts = _first_bounces(rally)
    assert rally.length_hits == len(rally.hits) >= 3
    assert rally.hits[0].is_serve and not any(h.is_serve for h in rally.hits[1:])
    assert [h.end for h in rally.hits] == [
        CourtEnd.NEAR if i % 2 == 0 else CourtEnd.FAR for i in range(rally.length_hits)
    ]
    assert firsts[1].target is LineCallTarget.SERVICE_BOX_DEUCE and firsts[1].is_in
    for shot in range(2, rally.length_hits):
        assert firsts[shot].is_in and firsts[shot].target is LineCallTarget.SINGLES_HALF
        assert firsts[shot].half is not rally.hits[shot - 1].end
    assert not firsts[rally.length_hits].is_in
    assert rally.end_reason is RallyEndReason.BALL_OUT
    assert rally.point_winner_end is not rally.hits[-1].end
    assert not rally.net_contacts
    assert scene.occlusions == ()


def test_bounce_truth_comes_from_physics_not_the_aim() -> None:
    (rally,) = generate_rally(SEED).rallies
    (ball,) = generate_rally(SEED).balls
    for bounce in rally.bounces:
        event = next(e for e in ball.trajectory.events if e.time_s == bounce.time_s)
        assert event.position_m == bounce.position_m
        assert bounce.position_m[2] == pytest.approx(BALL_RADIUS_M)


def test_serve_fault_truth() -> None:
    (rally,) = generate_rally(SEED, Scenario.SERVE_FAULT).rallies
    assert rally.length_hits == 1 and rally.is_serve_fault
    assert rally.point_winner_end is None
    first = _first_bounces(rally)[1]
    assert not first.is_in and first.target is LineCallTarget.SERVICE_BOX_DEUCE
    assert abs(first.position_m[1]) > 6.40  # long


def test_double_fault_is_two_rallies_and_the_second_scores() -> None:
    scene = generate_rally(SEED, Scenario.DOUBLE_FAULT)
    first, second = scene.rallies
    assert first.length_hits == second.length_hits == 1
    assert first.point_winner_end is None
    assert second.point_winner_end is CourtEnd.FAR  # the receiver
    assert first.server_end is second.server_end is CourtEnd.NEAR
    assert second.start_s - first.end_s > 2.5  # over the rally quiet split


def test_net_ball_truth() -> None:
    (rally,) = generate_rally(SEED, Scenario.NET_BALL).rallies
    (contact,) = rally.net_contacts
    assert contact.hitter_end is rally.hits[-1].end
    assert contact.time_s > rally.hits[-1].time_s
    assert rally.end_reason is RallyEndReason.NET
    assert rally.point_winner_end is not rally.hits[-1].end


def test_double_bounce_truth() -> None:
    (rally,) = generate_rally(SEED, Scenario.DOUBLE_BOUNCE).rallies
    last = [b for b in rally.bounces if b.shot_index == rally.length_hits]
    assert len(last) >= 2 and last[0].is_in
    assert last[0].half is last[1].half is not rally.hits[-1].end
    assert rally.end_reason is RallyEndReason.DOUBLE_BOUNCE
    assert rally.point_winner_end is rally.hits[-1].end


def test_far_half_occlusion_hides_flight_not_the_bounce() -> None:
    scene = generate_rally(SEED, Scenario.FAR_HALF_OCCLUSION)
    ((start, end),) = scene.occlusions
    (ball,) = scene.balls
    assert not scene.ball_visible(ball, 0.5 * (start + end))
    assert all(b.visible for b in scene.rallies[0].bounces)


def test_unseen_far_bounce_truth() -> None:
    scene = generate_rally(SEED, Scenario.UNSEEN_FAR_BOUNCE)
    (rally,) = scene.rallies
    assert rally.hits[-1].end is CourtEnd.NEAR
    final = [b for b in rally.bounces if b.shot_index == rally.length_hits]
    assert final[0].half is CourtEnd.FAR and not final[0].visible
    assert all(b.visible for b in rally.bounces if b.shot_index < rally.length_hits)


@pytest.mark.parametrize(
    ("scenario", "role"),
    [
        (Scenario.SECOND_BALL, BallRole.SECOND_BALL),
        (Scenario.ADJACENT_COURT_DECOY, BallRole.ADJACENT_DECOY),
        (Scenario.BIRD_LIKE_ARC, BallRole.BIRD),
    ],
)
def test_decoy_scenarios_add_one_extra_object(scenario: Scenario, role: BallRole) -> None:
    scene = generate_rally(SEED, scenario)
    roles = [b.role for b in scene.balls]
    assert roles == [BallRole.MAIN, role]
    decoy = scene.balls[1]
    rally = scene.rallies[0]
    assert decoy.trajectory.start_s < rally.end_s and decoy.trajectory.end_s > rally.start_s
    if role is BallRole.ADJACENT_DECOY:
        assert np.all(np.abs(decoy.trajectory.positions_m[:, 0]) > 5.485 + 1.0)
    if role is BallRole.BIRD:
        assert decoy.trajectory.events == ()
        assert np.min(decoy.trajectory.positions_m[:, 2]) > 3.0


def test_match_strings_rallies_with_quiet_gaps() -> None:
    scene = generate_match(SEED, [Scenario.CLEAN_RALLY, Scenario.SERVE_FAULT, Scenario.NET_BALL])
    assert len(scene.rallies) == 3
    servers = [r.server_end for r in scene.rallies]
    assert servers == [CourtEnd.NEAR, CourtEnd.FAR, CourtEnd.NEAR]
    for earlier, later in zip(scene.rallies, scene.rallies[1:], strict=False):
        assert later.start_s - earlier.end_s > 2.5


def test_same_seed_same_scene() -> None:
    a, b = generate_rally(SEED), generate_rally(SEED)
    assert np.array_equal(a.balls[0].trajectory.positions_m, b.balls[0].trajectory.positions_m)
    assert a.rallies == b.rallies
    other = generate_rally(SEED + 7)
    assert other.rallies != a.rallies


# ------------------------------------------------------------------------ render


def _small_camera() -> Any:
    return look_at(focal_px=focal_for_width(SMALL[0]), frame_size=SMALL)


def test_same_seed_gives_byte_identical_frames() -> None:
    scene = generate_rally(SEED, Scenario.SECOND_BALL)
    options = RenderOptions.degraded(SEED)
    first = CourtRenderer(scene, _small_camera(), options, seed=SEED)
    second = CourtRenderer(generate_rally(SEED, Scenario.SECOND_BALL), _small_camera(), options, SEED)
    for index in (0, 90, 150, 300):
        assert first.render_frame(index).tobytes() == second.render_frame(index).tobytes()
    other = CourtRenderer(scene, _small_camera(), RenderOptions.degraded(SEED), seed=SEED + 1)
    assert other.render_frame(90).tobytes() != first.render_frame(90).tobytes()


def test_no_file_is_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"court_synth tried to write: {args!r}")

    real_open = builtins.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            refuse(file, mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(cv2, "imwrite", refuse)
    monkeypatch.setattr(cv2, "VideoWriter", refuse)
    scene = generate_match(SEED, [Scenario.CLEAN_RALLY, Scenario.BIRD_LIKE_ARC])
    renderer = CourtRenderer(scene, _small_camera(), RenderOptions.degraded(SEED), seed=SEED)
    for index in range(0, renderer.frame_count, 97):
        renderer.render_frame(index)
    assert list(tmp_path.iterdir()) == []


def test_lines_are_drawn_where_the_camera_puts_them() -> None:
    camera = default_camera((1280, 720))
    renderer = CourtRenderer(generate_rally(SEED), camera, RenderOptions.clean(), SEED)
    static = renderer.static.astype(int)
    (x0, y0), (x1, _) = court_line_endpoints(CourtLine.NEAR_SERVICE_LINE)
    # The 5 cm line lies on the net side of its outer edge and is only ~1.2 px
    # tall here, so look at the brightest pixel in a +/-1 px column.
    on_line = camera.project(np.array([[0.5 * (x0 + x1) + 1.5, y0 + 0.025, 0.0]]))[0]
    u, v = int(round(on_line[0])), int(round(on_line[1]))
    assert max(static[row, u].min() for row in (v - 1, v, v + 1)) > 150  # white line
    off_line = camera.project(np.array([[1.5, y0 + 1.0, 0.0]]))[0]
    assert static[int(round(off_line[1])), int(round(off_line[0]))].min() < 100


def test_ball_is_drawn_at_its_true_pixel_and_hidden_when_occluded() -> None:
    camera = default_camera((1280, 720))
    scene = generate_rally(SEED, Scenario.FAR_HALF_OCCLUSION)
    renderer = CourtRenderer(scene, camera, RenderOptions(draw_players=False), SEED)
    ball = scene.balls[0]
    first_bounce = scene.rallies[0].bounces[1]
    index = int(np.searchsorted(renderer.frame_times, first_bounce.time_s - 0.1))
    uv = renderer.ball_uv(index, ball)
    assert np.all(np.isfinite(uv))
    frame = renderer.render_frame(index).astype(int)
    u, v = int(round(uv[0])), int(round(uv[1]))
    assert np.abs(frame[v, u] - renderer.static[v, u]).sum() > 60

    ((start, end),) = scene.occlusions
    hidden = int(np.searchsorted(renderer.frame_times, 0.5 * (start + end)))
    assert np.all(np.isnan(renderer.ball_uv(hidden, ball)))
    assert np.array_equal(renderer.render_frame(hidden), renderer.static)


@pytest.mark.parametrize("surface", list(Surface))
def test_every_surface_renders(surface: Surface) -> None:
    renderer = CourtRenderer(generate_rally(SEED), _small_camera(), RenderOptions.clean(surface=surface), SEED)
    assert renderer.static.shape == (SMALL[1], SMALL[0], 3)
    assert renderer.static.dtype == np.uint8


def test_pts_jitter_keeps_times_strictly_increasing() -> None:
    times = frame_times_s(10.0, 60.0, 0.004, SEED)
    assert np.all(np.diff(times) > 0.0)
    assert not np.allclose(times, np.arange(len(times)) / 60.0)


def test_shake_moves_the_whole_frame() -> None:
    scene = generate_rally(SEED)
    calm = CourtRenderer(scene, _small_camera(), RenderOptions(draw_players=False), SEED)
    shaky = CourtRenderer(scene, _small_camera(), RenderOptions(draw_players=False, shake_sigma_px=2.0), SEED)
    assert not np.array_equal(calm.render_frame(3), shaky.render_frame(3))
    assert np.any(shaky.shake != 0.0)


# ------------------------------------------------------------------------- truth


def test_signed_margin_and_contact_patch() -> None:
    region = half_region(CourtEnd.FAR, CourtFormat.SINGLES)
    assert truth.signed_margin_m((0.0, 11.0), region) == pytest.approx(0.885)
    assert truth.signed_margin_m((4.0, 5.0), region) == pytest.approx(0.115)
    assert truth.signed_margin_m((0.0, 11.9), region) == pytest.approx(-0.015)
    assert truth.is_in(-0.015)  # the ball's patch still touches the line
    assert not truth.is_in(-0.021)
    assert truth.signed_margin_m((0.0, -1.0), region) == pytest.approx(-1.0)  # wrong half


def test_match_times_is_one_to_one_and_greedy_by_nearest() -> None:
    result = truth.match_times([1.0, 2.0, 3.0], [1.01, 1.02, 2.5, 3.03], tolerance_s=2 / 60)
    assert result.pairs == ((0, 0), (2, 3))
    assert result.missed == (1,)
    assert result.false_positives == (1, 2)
    assert result.recall == pytest.approx(2 / 3)
    assert result.precision == pytest.approx(0.5)


def test_per_frame_recall() -> None:
    truth_uv = np.array([[10.0, 10.0], [np.nan, np.nan], [50.0, 50.0], [80.0, 80.0]])
    detections = [np.array([[11.0, 11.0]]), np.empty((0, 2)), np.array([[60.0, 50.0]]), np.empty((0, 2))]
    assert truth.per_frame_recall(truth_uv, detections) == pytest.approx(1 / 3)


def test_call_and_band_measures() -> None:
    verdicts = ["in", "out", "too_close_to_call", "in"]
    truth_in = [True, False, True, False]
    assert truth.confident_wrong_per_1000(verdicts, truth_in) == pytest.approx(250.0)
    margins = [0.5, -0.5, 0.01, -0.4]
    radii = [0.1, 0.1, 0.1, 0.1]
    assert truth.in_out_accuracy_outside_band(verdicts, truth_in, margins, radii) == pytest.approx(2 / 3)
    assert truth.band_coverage([0.45, -0.3, 0.0], [0.5, -0.5, 0.01], [0.1, 0.1, 0.1]) == pytest.approx(2 / 3)


def test_rally_length_scores() -> None:
    scores = truth.rally_length_scores([3, 5, 1], [3, 6, 3], [3, 5, 1], [4, 6, 2])
    assert scores.exact == pytest.approx(1 / 3)
    assert scores.within_one == pytest.approx(2 / 3)
    assert scores.range_contains_truth == pytest.approx(1.0)


def test_thresholds_are_the_section_11_2_table() -> None:
    assert truth.THRESHOLDS_T0["bounce_recall_min"] == 0.98
    assert truth.THRESHOLDS_T0["band_coverage_range"] == (0.92, 0.98)
    assert truth.THRESHOLDS_T2_1080P60["frame_recall_far_min"] == 0.80
    assert truth.THRESHOLDS_T2_1080P60["confident_wrong_per_1000_max"] == 10.0
