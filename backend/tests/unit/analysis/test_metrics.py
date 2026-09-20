"""Stage 13 unit tests (PIPELINE.md Stage 13, 4.5). Synthetic data only.

Every fixture is hand-built so the right answer is known analytically. The
sign-convention tests are the regression guard for the one bug that would make
every angle metric wrong: ``NormalizedSequence.points`` has y positive UP
(Stage 7 already flipped MediaPipe's downward y), so a pure upward swing must
read +90 deg and a pure downward swing -90 deg.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from app.analysis.handedness import HINT_CONFIDENCE_FLOOR
from app.analysis.metrics import (
    METRIC_VIEW_SENSITIVE,
    angular_range_deg,
    balance_sway_tu,
    compute_swing_metrics,
    contact_height_ratio,
    contact_point_forward_tu,
    elbow_angle_at_contact_deg,
    fit_trajectory_line,
    follow_through_height_tu,
    front_knee_side,
    head_stillness_tu,
    hip_rotation_deg,
    joint_angle_deg,
    knee_flexion_min_deg,
    line_angle_deg,
    max_absolute_signed,
    phase_span,
    position_spread_tu,
    shoulder_hip_separation_deg,
    shoulder_turn_deg,
    signed_angle_difference_deg,
    swing_path_fit,
    swing_span,
    vector_angle_deg,
    weight_transfer_tu,
    wrap_degrees,
    wrist_lag_deg,
    wrist_separation_at_contact_tu,
)
from app.analysis.normalize import (
    LEFT_ANKLE,
    LEFT_ELBOW,
    LEFT_HIP,
    LEFT_KNEE,
    LEFT_SHOULDER,
    LEFT_WRIST,
    NOSE,
    RIGHT_ANKLE,
    RIGHT_ELBOW,
    RIGHT_HIP,
    RIGHT_KNEE,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
)
from app.models.enums import Handedness, HandednessSource, SwingPhaseName
from app.models.internal import NormalizedSequence
from app.models.responses import (
    ContactDetection,
    HandednessResult,
    SwingMetrics,
    SwingPhase,
    SwingPhases,
)

NUM_LANDMARKS = 33
FRAME_COUNT = 24
CONTACT_FRAME = 14
FPS = 30.0

RIGHT_HANDED = HandednessResult(
    handedness=Handedness.RIGHT, confidence=0.95, source=HandednessSource.DETECTED
)


# --- fixtures ----------------------------------------------------------------


def build_sequence(
    *,
    frame_count: int = FRAME_COUNT,
    fps: float = FPS,
    swing_direction_sign: int = 1,
    wrist_xy: np.ndarray | None = None,
    visibility: float = 0.9,
) -> NormalizedSequence:
    """A static, upright body in the Stage 7 body frame: y UP, mid-hip at (0, 0)."""
    points = np.zeros((frame_count, NUM_LANDMARKS, 2), dtype=np.float64)
    points[:, LEFT_HIP] = (-0.5, 0.0)
    points[:, RIGHT_HIP] = (0.5, 0.0)
    points[:, LEFT_SHOULDER] = (-0.5, 1.0)
    points[:, RIGHT_SHOULDER] = (0.5, 1.0)
    points[:, LEFT_ELBOW] = (-0.5, 0.5)
    points[:, RIGHT_ELBOW] = (0.5, 0.5)
    points[:, LEFT_WRIST] = (-0.5, 0.5)
    points[:, NOSE] = (0.0, 1.5)
    # Right leg: hip (0.5, 0) -> knee (0.5, -1) -> ankle (1.5, -1) is a clean
    # right angle at the knee. Left knee sits behind, so the right leg is the
    # front leg for swing_direction_sign = +1.
    points[:, LEFT_KNEE] = (-0.5, -1.0)
    points[:, LEFT_ANKLE] = (-0.5, -2.0)
    points[:, RIGHT_KNEE] = (0.5, -1.0)
    points[:, RIGHT_ANKLE] = (1.5, -1.0)
    if wrist_xy is None:
        points[:, RIGHT_WRIST] = (0.5, 0.5)
    else:
        points[:, RIGHT_WRIST] = np.asarray(wrist_xy, dtype=np.float64)

    return NormalizedSequence(
        points=points,
        velocity=np.zeros_like(points),
        acceleration=np.zeros_like(points),
        timestamps_s=np.arange(frame_count, dtype=np.float64) / float(fps),
        valid=np.ones((frame_count,), dtype=bool),
        visibility=np.full((frame_count, NUM_LANDMARKS), visibility, dtype=np.float64),
        swing_direction_sign=int(swing_direction_sign),
        torso_scale_px=120.0,
        width_px=360,
        height_px=640,
        # Subject centred in a 360x640 pose frame, static. y DOWN.
        origin_px=np.tile(np.array([180.0, 320.0]), (frame_count, 1)),
    )


def with_points(
    seq: NormalizedSequence, landmark: int, xy: tuple[float, float] | np.ndarray
) -> NormalizedSequence:
    points = np.array(seq.points, copy=True)
    points[:, landmark] = np.asarray(xy, dtype=np.float64)
    return dataclasses.replace(seq, points=points)


def with_velocity(
    seq: NormalizedSequence, landmark: int, xy: tuple[float, float] | np.ndarray
) -> NormalizedSequence:
    velocity = np.array(seq.velocity, copy=True)
    velocity[:, landmark] = np.asarray(xy, dtype=np.float64)
    return dataclasses.replace(seq, velocity=velocity)


def hide(seq: NormalizedSequence, *landmarks: int, value: float = 0.1) -> NormalizedSequence:
    """Drop landmarks below VISIBILITY_THRESHOLD for the whole clip."""
    visibility = np.array(seq.visibility, copy=True)
    for landmark in landmarks:
        visibility[:, landmark] = value
    return dataclasses.replace(seq, visibility=visibility)


def build_phases(
    *,
    fps: float = FPS,
    ready: tuple[int, int] = (0, 3),
    takeback: tuple[int, int] = (4, 9),
    forward_swing: tuple[int, int] = (10, 13),
    contact: tuple[int, int] = (14, 15),
    follow_through: tuple[int, int] = (16, FRAME_COUNT - 1),
    tempo_ratio: float | None = None,
) -> SwingPhases:
    spans = (
        (SwingPhaseName.READY, ready),
        (SwingPhaseName.TAKEBACK, takeback),
        (SwingPhaseName.FORWARD_SWING, forward_swing),
        (SwingPhaseName.CONTACT, contact),
        (SwingPhaseName.FOLLOW_THROUGH, follow_through),
    )
    return SwingPhases(
        phases=[
            SwingPhase(
                name=name,
                start_frame=start,
                end_frame=end,
                start_time_s=start / fps,
                end_time_s=end / fps,
                duration_s=(end - start) / fps,
            )
            for name, (start, end) in spans
        ],
        tempo_ratio=tempo_ratio,
    )


def build_contact(
    frame_index: int = CONTACT_FRAME, *, peak_hand_speed_tu_s: float = 10.0
) -> ContactDetection:
    return ContactDetection(
        frame_index=frame_index,
        time_s=frame_index / FPS,
        contact_absolute_time_s=frame_index / FPS,
        confidence=0.8,
        peak_hand_speed_tu_s=peak_hand_speed_tu_s,
        peak_frame_index=frame_index,
        prominence_ratio=2.0,
    )


def straight_path(
    dx: float, dy: float, *, frame_count: int = FRAME_COUNT, origin: tuple[float, float] = (0.3, 0.2)
) -> np.ndarray:
    frames = np.arange(frame_count, dtype=np.float64)
    return np.stack([origin[0] + dx * frames, origin[1] + dy * frames], axis=1)


# --- geometry helpers --------------------------------------------------------


def test_vector_angle_is_positive_ninety_for_up() -> None:
    # y is positive UP in the body frame, so "up" is +y and must read +90.
    assert vector_angle_deg(np.array([0.0, 1.0])) == pytest.approx(90.0)


def test_vector_angle_is_negative_ninety_for_down() -> None:
    assert vector_angle_deg(np.array([0.0, -1.0])) == pytest.approx(-90.0)


def test_vector_angle_is_zero_along_positive_x() -> None:
    assert vector_angle_deg(np.array([2.0, 0.0])) == pytest.approx(0.0)


def test_vector_angle_of_degenerate_vector_is_none() -> None:
    assert vector_angle_deg(np.array([0.0, 0.0])) is None


def test_line_angle_uses_start_to_end_direction() -> None:
    assert line_angle_deg(np.array([0.0, 0.0]), np.array([1.0, 1.0])) == pytest.approx(45.0)


def test_wrap_degrees_maps_into_half_open_interval() -> None:
    assert wrap_degrees(190.0) == pytest.approx(-170.0)
    assert wrap_degrees(-190.0) == pytest.approx(170.0)
    assert wrap_degrees(180.0) == pytest.approx(180.0)


def test_signed_angle_difference_keeps_sign_across_the_wrap() -> None:
    assert signed_angle_difference_deg(170.0, -170.0) == pytest.approx(-20.0)
    assert signed_angle_difference_deg(10.0, -10.0) == pytest.approx(20.0)


def test_joint_angle_of_perpendicular_limbs_is_a_right_angle() -> None:
    angle = joint_angle_deg(
        np.array([0.0, 1.0]), np.array([0.0, 0.0]), np.array([1.0, 0.0])
    )
    assert angle == pytest.approx(90.0)


def test_joint_angle_of_straight_limb_is_one_eighty() -> None:
    angle = joint_angle_deg(
        np.array([0.0, 1.0]), np.array([0.0, 0.0]), np.array([0.0, -1.0])
    )
    assert angle == pytest.approx(180.0)


def test_joint_angle_of_degenerate_limb_is_none_not_zero() -> None:
    assert (
        joint_angle_deg(np.array([0.0, 0.0]), np.array([0.0, 0.0]), np.array([1.0, 0.0]))
        is None
    )


def test_angular_range_unwraps_across_the_boundary() -> None:
    assert angular_range_deg(np.array([179.0, -179.0])) == pytest.approx(2.0)


def test_angular_range_of_empty_is_none() -> None:
    assert angular_range_deg(np.zeros((0,))) is None


def test_max_absolute_signed_preserves_the_sign() -> None:
    assert max_absolute_signed(np.array([3.0, -7.0, 5.0])) == pytest.approx(-7.0)


def test_position_spread_is_rms_distance_from_the_mean() -> None:
    # Two points 2 apart on x: var_x = 1, var_y = 0 -> sqrt(1) = 1.
    assert position_spread_tu(np.array([[0.0, 0.0], [2.0, 0.0]])) == pytest.approx(1.0)


def test_position_spread_of_a_single_point_is_none() -> None:
    assert position_spread_tu(np.array([[1.0, 1.0]])) is None


# --- line fit: THE sign convention ------------------------------------------


def test_fit_trajectory_line_pure_upward_is_plus_ninety() -> None:
    fit = fit_trajectory_line(np.stack([np.full(6, 0.4), np.linspace(0.0, 1.0, 6)], axis=1))
    assert fit is not None
    angle, rms = fit
    assert angle == pytest.approx(90.0)
    assert rms == pytest.approx(0.0, abs=1e-9)


def test_fit_trajectory_line_pure_downward_is_minus_ninety() -> None:
    fit = fit_trajectory_line(np.stack([np.full(6, 0.4), np.linspace(1.0, 0.0, 6)], axis=1))
    assert fit is not None
    assert fit[0] == pytest.approx(-90.0)


def test_fit_trajectory_line_forty_five_degree_diagonal() -> None:
    # Off-axis: catches an x/y swap that the two pure-axis cases cannot.
    steps = np.linspace(0.0, 1.0, 6)
    fit = fit_trajectory_line(np.stack([steps, steps], axis=1))
    assert fit is not None
    assert fit[0] == pytest.approx(45.0)


def test_fit_trajectory_line_low_to_high_backwards_is_still_positive() -> None:
    # Travelling up and to the LEFT is still low-to-high, so still positive.
    steps = np.linspace(0.0, 1.0, 6)
    fit = fit_trajectory_line(np.stack([-steps, steps], axis=1))
    assert fit is not None
    assert fit[0] == pytest.approx(135.0)


def test_fit_trajectory_line_residual_is_rms_perpendicular_distance() -> None:
    # Zero x/y covariance, so the fitted line is exactly the x axis and each
    # point sits one unit off it: RMS perpendicular residual = 1.0.
    points = np.array([[0.0, 1.0], [1.0, -1.0], [2.0, -1.0], [3.0, 1.0]])
    fit = fit_trajectory_line(points)
    assert fit is not None
    assert fit[0] == pytest.approx(0.0)
    assert fit[1] == pytest.approx(1.0)


def test_fit_trajectory_line_needs_three_points() -> None:
    assert fit_trajectory_line(np.array([[0.0, 0.0], [1.0, 1.0]])) is None


def test_fit_trajectory_line_of_a_stationary_hand_is_none_not_zero() -> None:
    assert fit_trajectory_line(np.zeros((6, 2))) is None


# --- swing_path_angle_deg end to end ----------------------------------------


def test_pure_upward_swing_reports_positive_ninety_degrees() -> None:
    seq = build_sequence(wrist_xy=straight_path(0.0, 0.1))
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    assert metrics.swing_path_angle_deg == pytest.approx(90.0)


def test_pure_downward_swing_reports_negative_ninety_degrees() -> None:
    seq = build_sequence(wrist_xy=straight_path(0.0, -0.1))
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    assert metrics.swing_path_angle_deg == pytest.approx(-90.0)


def test_forty_five_degree_swing_reports_forty_five_degrees() -> None:
    seq = build_sequence(wrist_xy=straight_path(0.1, 0.1))
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    assert metrics.swing_path_angle_deg == pytest.approx(45.0)


def test_straight_swing_has_zero_plane_deviation() -> None:
    seq = build_sequence(wrist_xy=straight_path(0.1, 0.1))
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    assert metrics.swing_plane_deviation_tu == pytest.approx(0.0, abs=1e-9)


def test_swing_path_fit_window_is_contact_minus_six_to_plus_three() -> None:
    # Frames outside [contact-6, contact+3] are wild; the fit must ignore them.
    path = straight_path(0.1, 0.1)
    path[: CONTACT_FRAME - 6] = (99.0, -99.0)
    path[CONTACT_FRAME + 4 :] = (-99.0, 99.0)
    seq = build_sequence(wrist_xy=path)
    fit = swing_path_fit(seq, Handedness.RIGHT, CONTACT_FRAME)
    assert fit is not None
    assert fit[0] == pytest.approx(45.0)


# --- contact-frame metrics ---------------------------------------------------


def test_elbow_angle_at_contact_right_angle() -> None:
    seq = build_sequence()
    seq = with_points(seq, RIGHT_SHOULDER, (0.5, 1.0))
    seq = with_points(seq, RIGHT_ELBOW, (0.5, 0.5))
    seq = with_points(seq, RIGHT_WRIST, (1.0, 0.5))
    assert elbow_angle_at_contact_deg(seq, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(90.0)


def test_elbow_angle_uses_the_left_arm_for_a_left_handed_player() -> None:
    seq = build_sequence()
    seq = with_points(seq, LEFT_SHOULDER, (-0.5, 1.0))
    seq = with_points(seq, LEFT_ELBOW, (-0.5, 0.5))
    seq = with_points(seq, LEFT_WRIST, (-1.0, 0.5))
    assert elbow_angle_at_contact_deg(seq, Handedness.LEFT, CONTACT_FRAME) == pytest.approx(90.0)


def test_contact_height_ratio_at_shoulder_height_is_one() -> None:
    seq = build_sequence(wrist_xy=np.tile(np.array([0.6, 1.0]), (FRAME_COUNT, 1)))
    assert contact_height_ratio(seq, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(1.0)


def test_contact_height_ratio_at_hip_height_is_zero() -> None:
    seq = build_sequence(wrist_xy=np.tile(np.array([0.6, 0.0]), (FRAME_COUNT, 1)))
    ratio = contact_height_ratio(seq, Handedness.RIGHT, CONTACT_FRAME)
    assert ratio == pytest.approx(0.0)
    assert ratio is not None  # a measured 0.0, distinct from unavailable


def test_contact_height_ratio_above_the_shoulders_exceeds_one() -> None:
    seq = build_sequence(wrist_xy=np.tile(np.array([0.6, 1.5]), (FRAME_COUNT, 1)))
    assert contact_height_ratio(seq, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(1.5)


def test_contact_point_forward_multiplies_by_swing_direction_sign() -> None:
    wrist = np.tile(np.array([0.7, 0.5]), (FRAME_COUNT, 1))
    forward = build_sequence(wrist_xy=wrist, swing_direction_sign=1)
    mirrored = build_sequence(wrist_xy=wrist, swing_direction_sign=-1)
    assert contact_point_forward_tu(forward, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(0.7)
    assert contact_point_forward_tu(mirrored, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(-0.7)


def test_contact_point_forward_at_the_hips_is_a_measured_zero() -> None:
    seq = build_sequence(wrist_xy=np.tile(np.array([0.0, 0.5]), (FRAME_COUNT, 1)))
    value = contact_point_forward_tu(seq, Handedness.RIGHT, CONTACT_FRAME)
    assert value is not None
    assert value == pytest.approx(0.0)


def test_wrist_separation_at_contact_is_the_distance_between_wrists() -> None:
    seq = build_sequence(wrist_xy=np.tile(np.array([0.5, 0.5]), (FRAME_COUNT, 1)))
    # left wrist sits at (-0.5, 0.5)
    assert wrist_separation_at_contact_tu(seq, CONTACT_FRAME) == pytest.approx(1.0)


def test_wrist_lag_is_measured_three_frames_before_contact() -> None:
    seq = build_sequence()
    seq = with_points(seq, RIGHT_ELBOW, (0.0, 0.5))
    seq = with_points(seq, RIGHT_WRIST, (1.0, 0.5))  # forearm points along +x
    velocity = np.zeros_like(seq.velocity)
    velocity[CONTACT_FRAME - 3, RIGHT_WRIST] = (0.0, 4.0)  # hand travelling +y
    seq = dataclasses.replace(seq, velocity=velocity)
    assert wrist_lag_deg(seq, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(90.0)


def test_wrist_lag_with_a_motionless_hand_is_none() -> None:
    seq = build_sequence()
    assert wrist_lag_deg(seq, Handedness.RIGHT, CONTACT_FRAME) is None


def test_wrist_lag_before_the_clip_start_is_none() -> None:
    seq = build_sequence()
    seq = with_velocity(seq, RIGHT_WRIST, (0.0, 4.0))
    assert wrist_lag_deg(seq, Handedness.RIGHT, 2) is None


# --- rotation metrics --------------------------------------------------------


def test_shoulder_hip_separation_keeps_the_sign_of_the_largest_twist() -> None:
    seq = build_sequence()
    points = np.array(seq.points, copy=True)
    # Rotate the shoulder line -30 deg relative to the hip line during takeback.
    angle = math.radians(-30.0)
    half = np.array([math.cos(angle), math.sin(angle)]) * 0.5
    points[4:10, LEFT_SHOULDER] = np.array([0.0, 1.0]) - half
    points[4:10, RIGHT_SHOULDER] = np.array([0.0, 1.0]) + half
    seq = dataclasses.replace(seq, points=points)
    value = shoulder_hip_separation_deg(seq, build_phases())
    assert value == pytest.approx(-30.0)


def test_shoulder_turn_is_the_angle_range_across_the_swing() -> None:
    seq = build_sequence()
    points = np.array(seq.points, copy=True)
    for frame, degrees in ((6, 0.0), (12, 40.0), (18, 10.0)):
        angle = math.radians(degrees)
        half = np.array([math.cos(angle), math.sin(angle)]) * 0.5
        points[frame, LEFT_SHOULDER] = np.array([0.0, 1.0]) - half
        points[frame, RIGHT_SHOULDER] = np.array([0.0, 1.0]) + half
    seq = dataclasses.replace(seq, points=points)
    # The remaining frames sit at 0 deg, so the range is 0 -> 40.
    assert shoulder_turn_deg(seq, build_phases()) == pytest.approx(40.0)


def test_hip_rotation_of_a_static_body_is_a_measured_zero() -> None:
    value = hip_rotation_deg(build_sequence(), build_phases())
    assert value is not None
    assert value == pytest.approx(0.0)


# --- speed, knees, follow-through, head --------------------------------------


def test_peak_hand_speed_is_copied_from_the_stage_nine_contact_detection() -> None:
    # Stage 13 does NOT recompute this: Stage 9 already applied its own
    # reliability rule, and recomputing it here would let the two diverge.
    seq = build_sequence()
    velocity = np.zeros_like(seq.velocity)
    velocity[:, RIGHT_WRIST, 0] = np.linspace(0.0, 9.0, FRAME_COUNT)
    seq = dataclasses.replace(seq, velocity=velocity)
    metrics, _ = compute_swing_metrics(
        seq, RIGHT_HANDED, build_contact(peak_hand_speed_tu_s=7.25), build_phases()
    )
    # 7.25 from ContactDetection, NOT the 9.0 peak present in seq.velocity.
    assert metrics.peak_hand_speed_tu_s == pytest.approx(7.25)


def test_peak_hand_speed_is_not_rescaled_into_any_other_unit() -> None:
    seq = with_velocity(build_sequence(), RIGHT_WRIST, (3.0, 4.0))
    metrics, _ = compute_swing_metrics(
        seq, RIGHT_HANDED, build_contact(peak_hand_speed_tu_s=5.0), build_phases()
    )
    # 5.0 TU/s exactly: no mph (x2.237-ish) or m/s conversion anywhere.
    assert metrics.peak_hand_speed_tu_s == pytest.approx(5.0)


def test_peak_hand_speed_of_zero_from_stage_nine_stays_a_measured_zero() -> None:
    seq = with_velocity(build_sequence(), RIGHT_WRIST, (3.0, 4.0))
    metrics, _ = compute_swing_metrics(
        seq, RIGHT_HANDED, build_contact(peak_hand_speed_tu_s=0.0), build_phases()
    )
    assert metrics.peak_hand_speed_tu_s is not None
    assert metrics.peak_hand_speed_tu_s == pytest.approx(0.0)


def test_front_knee_is_the_one_further_toward_the_target() -> None:
    seq = build_sequence(swing_direction_sign=1)
    assert front_knee_side(seq, CONTACT_FRAME) == (RIGHT_HIP, RIGHT_KNEE, RIGHT_ANKLE)
    mirrored = build_sequence(swing_direction_sign=-1)
    assert front_knee_side(mirrored, CONTACT_FRAME) == (LEFT_HIP, LEFT_KNEE, LEFT_ANKLE)


def test_knee_flexion_min_is_the_smallest_front_knee_angle_in_forward_swing() -> None:
    seq = build_sequence()
    points = np.array(seq.points, copy=True)
    # Straighten the knee everywhere, then bend it to a right angle at frame 12,
    # which is inside forward_swing (10..13).
    points[:, RIGHT_ANKLE] = (0.5, -2.0)
    points[12, RIGHT_ANKLE] = (1.5, -1.0)
    seq = dataclasses.replace(seq, points=points)
    assert knee_flexion_min_deg(seq, build_phases(), CONTACT_FRAME) == pytest.approx(90.0)


def test_follow_through_height_is_the_max_wrist_y_after_contact() -> None:
    path = np.tile(np.array([0.5, 0.5]), (FRAME_COUNT, 1))
    path[20] = (0.5, 2.0)  # peak after contact; shoulders sit at y = 1.0
    path[3] = (0.5, 5.0)   # a higher point BEFORE contact must be ignored
    seq = build_sequence(wrist_xy=path)
    assert follow_through_height_tu(seq, Handedness.RIGHT, CONTACT_FRAME) == pytest.approx(1.0)


def test_head_stillness_is_the_nose_spread_during_forward_swing() -> None:
    seq = build_sequence()
    points = np.array(seq.points, copy=True)
    points[10:12, NOSE] = (0.0, 1.5)
    points[12:14, NOSE] = (1.0, 1.5)
    seq = dataclasses.replace(seq, points=points)
    # forward_swing is frames 10..13: x = [0, 0, 1, 1] -> var 0.25 -> 0.5
    assert head_stillness_tu(seq, build_phases()) == pytest.approx(0.5)


def test_head_stillness_of_a_motionless_head_is_a_measured_zero() -> None:
    value = head_stillness_tu(build_sequence(), build_phases())
    assert value is not None
    assert value == pytest.approx(0.0)


# --- pass-through and non-measurable ----------------------------------------


def test_tempo_ratio_is_passed_through_from_stage_twelve() -> None:
    metrics, _ = compute_swing_metrics(
        build_sequence(), RIGHT_HANDED, build_contact(), build_phases(tempo_ratio=1.61)
    )
    assert metrics.tempo_ratio == pytest.approx(1.61)


def test_mid_hip_metrics_are_none_because_the_mid_hip_is_the_origin() -> None:
    # Stage 7 pins mid-hip at (0, 0) every frame, so these two are unrecoverable
    # from a NormalizedSequence. They must be None, never a flattering 0.0.
    seq = build_sequence()
    assert weight_transfer_tu(seq, build_phases()) is None
    assert balance_sway_tu(seq, build_phases()) is None


def test_takeback_displacement_is_none_pending_a_definition() -> None:
    metrics, _ = compute_swing_metrics(
        build_sequence(), RIGHT_HANDED, build_contact(), build_phases()
    )
    assert metrics.takeback_displacement_tu is None


# --- phase access ------------------------------------------------------------


def test_phase_span_returns_inclusive_frame_bounds() -> None:
    assert phase_span(build_phases(), SwingPhaseName.FORWARD_SWING) == (10, 13)


def test_phase_span_of_missing_phases_is_none() -> None:
    assert phase_span(None, SwingPhaseName.TAKEBACK) is None
    assert phase_span(SwingPhases(phases=[]), SwingPhaseName.TAKEBACK) is None


def test_swing_span_runs_from_takeback_start_to_follow_through_end() -> None:
    assert swing_span(build_phases()) == (4, FRAME_COUNT - 1)


def test_phase_dependent_metrics_are_none_without_phases() -> None:
    metrics, _ = compute_swing_metrics(build_sequence(), RIGHT_HANDED, build_contact(), None)
    assert metrics.shoulder_hip_separation_deg is None
    assert metrics.shoulder_turn_deg is None
    assert metrics.hip_rotation_deg is None
    assert metrics.knee_flexion_min_deg is None
    assert metrics.head_stillness_tu is None
    assert metrics.tempo_ratio is None


# --- None discipline: never a default, never zero ---------------------------


def test_invisible_wrist_makes_every_wrist_metric_none_not_zero() -> None:
    seq = hide(build_sequence(wrist_xy=straight_path(0.1, 0.1)), RIGHT_WRIST)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    for field in (
        "contact_height_ratio",
        "contact_point_forward_tu",
        "swing_path_angle_deg",
        "swing_plane_deviation_tu",
        "follow_through_height_tu",
        "wrist_separation_at_contact_tu",
        "elbow_angle_at_contact_deg",
    ):
        assert getattr(metrics, field) is None, field


def test_invisible_racket_wrist_leaves_peak_hand_speed_to_stage_nine() -> None:
    # The wrist metrics all collapse to None, but peak_hand_speed_tu_s is a
    # pass-through of Stage 9's value and is NOT re-gated on visibility here.
    seq = hide(with_velocity(build_sequence(), RIGHT_WRIST, (3.0, 4.0)), RIGHT_WRIST)
    metrics, _ = compute_swing_metrics(
        seq, RIGHT_HANDED, build_contact(peak_hand_speed_tu_s=6.5), build_phases()
    )
    assert metrics.contact_height_ratio is None
    assert metrics.peak_hand_speed_tu_s == pytest.approx(6.5)


def test_peak_hand_speed_of_none_from_stage_nine_passes_through_as_none() -> None:
    # model_copy bypasses validation to simulate a Stage 9 value that is absent:
    # the pass-through must carry it through untouched, never coerce it to 0.0.
    contact = build_contact().model_copy(update={"peak_hand_speed_tu_s": None})
    metrics, _ = compute_swing_metrics(
        build_sequence(), RIGHT_HANDED, contact, build_phases()
    )
    assert metrics.peak_hand_speed_tu_s is None


def test_invisible_elbow_makes_the_arm_metrics_none() -> None:
    seq = build_sequence()
    seq = with_velocity(seq, RIGHT_WRIST, (0.0, 4.0))
    seq = hide(seq, RIGHT_ELBOW)
    assert elbow_angle_at_contact_deg(seq, Handedness.RIGHT, CONTACT_FRAME) is None
    assert wrist_lag_deg(seq, Handedness.RIGHT, CONTACT_FRAME) is None


def test_invisible_hips_make_the_rotation_metrics_none_not_zero() -> None:
    seq = hide(build_sequence(), LEFT_HIP, RIGHT_HIP)
    assert shoulder_hip_separation_deg(seq, build_phases()) is None
    assert hip_rotation_deg(seq, build_phases()) is None


def test_invisible_shoulders_make_the_shoulder_metrics_none_not_zero() -> None:
    seq = hide(build_sequence(), LEFT_SHOULDER, RIGHT_SHOULDER)
    assert shoulder_turn_deg(seq, build_phases()) is None
    assert shoulder_hip_separation_deg(seq, build_phases()) is None
    assert contact_height_ratio(seq, Handedness.RIGHT, CONTACT_FRAME) is None
    assert follow_through_height_tu(seq, Handedness.RIGHT, CONTACT_FRAME) is None


def test_invisible_legs_make_knee_flexion_none_not_zero() -> None:
    seq = hide(build_sequence(), LEFT_KNEE, RIGHT_KNEE)
    assert front_knee_side(seq, CONTACT_FRAME) is None
    assert knee_flexion_min_deg(seq, build_phases(), CONTACT_FRAME) is None


def test_invisible_nose_makes_head_stillness_none_not_zero() -> None:
    seq = hide(build_sequence(), NOSE)
    assert head_stillness_tu(seq, build_phases()) is None


def test_a_fully_occluded_clip_yields_all_none() -> None:
    seq = hide(build_sequence(), *range(NUM_LANDMARKS))
    # peak_hand_speed_tu_s is sourced from Stage 9, not from this sequence, so
    # it is excluded: an occluded clip says nothing about Stage 9's value.
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    measured = metrics.model_dump()
    measured.pop("peak_hand_speed_tu_s")
    expected = SwingMetrics().model_dump()
    expected.pop("peak_hand_speed_tu_s")
    assert measured == expected


# --- handedness confidence warnings -----------------------------------------


def test_confident_handedness_produces_no_warnings() -> None:
    _, warnings = compute_swing_metrics(
        build_sequence(), RIGHT_HANDED, build_contact(), build_phases()
    )
    assert warnings == []


def test_confidence_exactly_at_the_floor_is_not_warned_about() -> None:
    # The floor is the same threshold handedness.py uses: strictly below warns.
    handedness = HandednessResult(
        handedness=Handedness.RIGHT,
        confidence=HINT_CONFIDENCE_FLOOR,
        source=HandednessSource.DETECTED,
    )
    _, warnings = compute_swing_metrics(
        build_sequence(), handedness, build_contact(), build_phases()
    )
    assert warnings == []


def test_low_confidence_detection_warns_that_no_hint_was_applied() -> None:
    handedness = HandednessResult(
        handedness=Handedness.LEFT, confidence=0.31, source=HandednessSource.DETECTED
    )
    _, warnings = compute_swing_metrics(
        build_sequence(), handedness, build_contact(), build_phases()
    )
    assert warnings == [
        "low handedness confidence (0.31 < 0.5); no hint applied (source detected); "
        "handedness-sensitive metrics may be unreliable"
    ]


def test_low_confidence_warns_even_when_a_hint_already_overrode_detection() -> None:
    # The hint rescued the answer, but Stage 8's own read was still weak, and
    # that is a separate fact the caller is entitled to know.
    handedness = HandednessResult(
        handedness=Handedness.RIGHT, confidence=0.4, source=HandednessSource.USER_HINT
    )
    _, warnings = compute_swing_metrics(
        build_sequence(), handedness, build_contact(), build_phases()
    )
    assert warnings == [
        "low handedness confidence (0.40 < 0.5); user hint applied (source user_hint); "
        "handedness-sensitive metrics may be unreliable"
    ]


def test_hint_overrode_detection_source_also_reads_as_hint_applied() -> None:
    handedness = HandednessResult(
        handedness=Handedness.RIGHT,
        confidence=0.49,
        source=HandednessSource.HINT_OVERRODE_DETECTION,
    )
    _, warnings = compute_swing_metrics(
        build_sequence(), handedness, build_contact(), build_phases()
    )
    assert warnings == [
        "low handedness confidence (0.49 < 0.5); user hint applied "
        "(source hint_overrode_detection); handedness-sensitive metrics may be unreliable"
    ]


def test_warnings_survive_a_degenerate_sequence_that_measures_nothing() -> None:
    handedness = HandednessResult(
        handedness=Handedness.RIGHT, confidence=0.2, source=HandednessSource.DETECTED
    )
    metrics, warnings = compute_swing_metrics(
        dataclasses.replace(build_sequence(), points=np.zeros((0,)), velocity=np.zeros((0,))),
        handedness,
        build_contact(),
        build_phases(),
    )
    assert isinstance(metrics, SwingMetrics)
    assert len(warnings) == 1


# --- contract ----------------------------------------------------------------


def test_compute_swing_metrics_never_raises_on_a_degenerate_sequence() -> None:
    seq = build_sequence(frame_count=0)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(0), build_phases())
    assert isinstance(metrics, SwingMetrics)


def test_compute_swing_metrics_never_raises_on_a_malformed_sequence() -> None:
    seq = dataclasses.replace(build_sequence(), points=np.zeros((0,)), velocity=np.zeros((0,)))
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(), build_phases())
    assert isinstance(metrics, SwingMetrics)


def test_compute_swing_metrics_never_raises_when_contact_is_out_of_range() -> None:
    seq = build_sequence(wrist_xy=straight_path(0.0, 0.1))
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, build_contact(9999), build_phases())
    assert isinstance(metrics, SwingMetrics)


def test_every_swing_metrics_field_is_optional_float() -> None:
    blank = SwingMetrics()
    assert all(value is None for value in blank.model_dump().values())


def test_view_sensitive_flags_match_the_stage_13_table() -> None:
    expected = {
        "shoulder_hip_separation_deg": False,
        "shoulder_turn_deg": True,
        "hip_rotation_deg": True,
        "elbow_angle_at_contact_deg": True,
        "wrist_lag_deg": True,
        "contact_height_ratio": False,
        "contact_point_forward_tu": True,
        "peak_hand_speed_tu_s": True,
        "swing_path_angle_deg": False,
        "swing_plane_deviation_tu": True,
        "knee_flexion_min_deg": True,
        "weight_transfer_tu": True,
        "follow_through_height_tu": False,
        "balance_sway_tu": False,
        "head_stillness_tu": False,
        "tempo_ratio": False,
        "wrist_separation_at_contact_tu": False,
    }
    assert METRIC_VIEW_SENSITIVE == expected


def test_view_sensitive_keys_are_all_swing_metrics_fields() -> None:
    assert set(METRIC_VIEW_SENSITIVE) <= set(SwingMetrics.model_fields)
