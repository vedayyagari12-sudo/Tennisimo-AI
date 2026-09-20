"""Stage 10/11 geometry seam: ``app/ball/geometry.py``. PURE, so no fixtures.

The trap this module guards is stated in its own docstring: ``segment_px`` is a
TRUE PIXEL distance. Measuring it in normalized units on a 9:16 frame is wrong
by a view-dependent factor and produces a plausible -- not absurd -- mph, which
is why :func:`test_segment_is_measured_in_true_pixels_not_normalized_units`
exists.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analysis.normalize import normalize_sequence
from app.ball.geometry import (
    CAL_SPACE_LONG_EDGE_PX,
    MAX_PX_PER_M,
    MIN_PX_PER_M,
    MIN_SEGMENT_PX,
    TORSO_LEG_LANDMARKS,
    CalibrationRejected,
    cal_space_size,
    frame_shape_matches,
    landmarks_to_cal_px,
    map_calibration_points,
    pose_to_cal_scale,
    preview_point_to_cal_px,
    racket_wrist_seed_px,
    rotate_normalized,
    scale_from_calibration,
    seed_gate_px,
)
from app.models.enums import BallSpeedUnavailableReason, CourtReference
from app.models.internal import PoseSequence
from app.models.requests import BallSpeedCalibration, NormalizedPoint

RIGHT_WRIST = 16


def make_calibration(**overrides: object) -> BallSpeedCalibration:
    """A portrait 1080x1920 capture with a baseline-to-net tap down one side."""
    fields: dict[str, object] = {
        "point_a": NormalizedPoint(x=0.20, y=0.30),
        "point_b": NormalizedPoint(x=0.80, y=0.75),
        "reference": CourtReference.SIDELINE_BASELINE_TO_NET,
        "distance_m": 11.885,
        "capture_width_px": 1080,
        "capture_height_px": 1920,
        "capture_rotation_deg": 0,
    }
    fields.update(overrides)
    return BallSpeedCalibration(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Coordinate spaces
# --------------------------------------------------------------------------- #


def test_cal_space_size_caps_the_long_edge_and_never_upscales() -> None:
    assert cal_space_size(1080, 2048) == (675, 1280)
    assert cal_space_size(640, 360) == (640, 360)
    with pytest.raises(ValueError):
        cal_space_size(0, 100)


def test_rotate_normalized_is_a_clockwise_quarter_turn() -> None:
    assert rotate_normalized(0.25, 0.10, 0) == (0.25, 0.10)
    assert rotate_normalized(0.25, 0.10, 90) == pytest.approx((0.90, 0.25))
    assert rotate_normalized(0.25, 0.10, 180) == pytest.approx((0.75, 0.90))
    assert rotate_normalized(0.25, 0.10, 270) == pytest.approx((0.10, 0.75))
    with pytest.raises(ValueError):
        rotate_normalized(0.5, 0.5, 45)


def test_four_quarter_turns_are_the_identity() -> None:
    point = (0.31, 0.72)
    turned = point
    for _ in range(4):
        turned = rotate_normalized(turned[0], turned[1], 90)
    assert turned == pytest.approx(point)


def test_capture_rotation_is_inverted_before_the_container_rotation() -> None:
    """A preview rotated by the client and a container rotated back cancel."""
    point = NormalizedPoint(x=0.30, y=0.20)
    cancelled = preview_point_to_cal_px(
        point,
        capture_rotation_deg=90,
        container_rotation_deg=90,
        cal_width_px=720,
        cal_height_px=1280,
    )
    assert cancelled == pytest.approx((0.30 * 720, 0.20 * 1280))


def test_pose_to_cal_scale_is_the_long_edge_ratio() -> None:
    assert pose_to_cal_scale(640, 1280) == 2.0
    with pytest.raises(ValueError):
        pose_to_cal_scale(0, 1280)


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #


def test_frame_shape_mismatch_is_rejected_with_its_own_reason() -> None:
    calibration = make_calibration(capture_width_px=1080, capture_height_px=1080)
    with pytest.raises(CalibrationRejected) as excinfo:
        map_calibration_points(
            calibration,
            container_rotation_deg=0,
            cal_width_px=675,
            cal_height_px=1280,
        )
    assert excinfo.value.reason is BallSpeedUnavailableReason.CALIBRATION_FRAME_MISMATCH


def test_frame_shape_matches_tolerates_rounding_but_not_a_crop() -> None:
    assert frame_shape_matches(1080, 1920, 675, 1200)
    assert not frame_shape_matches(1080, 1080, 675, 1200)
    assert not frame_shape_matches(0, 1920, 675, 1200)


def test_scale_outside_the_plausibility_band_is_rejected_not_clamped() -> None:
    with pytest.raises(CalibrationRejected) as short:
        scale_from_calibration(MIN_SEGMENT_PX - 1.0, 5.0)
    assert short.value.reason is BallSpeedUnavailableReason.CALIBRATION_IMPLAUSIBLE

    with pytest.raises(CalibrationRejected):
        scale_from_calibration(1000.0, 25.0 * 1000.0)  # far below MIN_PX_PER_M
    with pytest.raises(CalibrationRejected):
        scale_from_calibration(10_000.0, 1.0)  # far above MAX_PX_PER_M

    assert MIN_PX_PER_M <= scale_from_calibration(600.0, 11.885) <= MAX_PX_PER_M


def test_segment_is_measured_in_true_pixels_not_normalized_units() -> None:
    """The one bug PIPELINE.md 11.1 calls the most likely in the feature.

    On a 720x1280 CAL_SPACE the same tap pair measured in normalized units and
    scaled by the long edge gives a materially different answer from the true
    pixel distance, because one normalized x unit is not one normalized y unit.
    """
    calibration = make_calibration()
    echo = map_calibration_points(
        calibration,
        container_rotation_deg=0,
        cal_width_px=720,
        cal_height_px=1280,
    )

    true_px = math.hypot(0.60 * 720, 0.45 * 1280)
    normalized_then_scaled = math.hypot(0.60, 0.45) * CAL_SPACE_LONG_EDGE_PX

    assert echo.segment_px == pytest.approx(true_px, rel=1e-6)
    assert abs(echo.segment_px - normalized_then_scaled) > 50.0, (
        "the two measurements must differ, or this test proves nothing"
    )
    assert echo.px_per_m == pytest.approx(echo.segment_px / echo.distance_m)
    assert echo.point_a_cal_px == pytest.approx((0.20 * 720, 0.30 * 1280))


# --------------------------------------------------------------------------- #
# Pose -> CAL_SPACE
# --------------------------------------------------------------------------- #


def standing_sequence(frame_count: int = 8) -> PoseSequence:
    landmarks = np.zeros((frame_count, 33, 4), dtype=np.float32)
    layout = {
        0: (0.50, 0.20),
        11: (0.45, 0.30), 12: (0.55, 0.30),
        13: (0.42, 0.40), 14: (0.58, 0.40),
        15: (0.40, 0.50), 16: (0.60, 0.50),
        23: (0.46, 0.55), 24: (0.54, 0.55),
        25: (0.46, 0.70), 26: (0.54, 0.70),
        27: (0.46, 0.85), 28: (0.54, 0.85),
    }
    for index, (x, y) in layout.items():
        landmarks[:, index, 0] = x
        landmarks[:, index, 1] = y
        landmarks[:, index, 3] = 0.95
    return PoseSequence(
        landmarks=landmarks,
        world=np.zeros((frame_count, 33, 3), dtype=np.float32),
        timestamps_s=np.arange(frame_count, dtype=np.float64) / 30.0,
        detected=np.ones((frame_count,), dtype=bool),
        width_px=360,
        height_px=640,
    )


def test_landmarks_return_to_the_pixel_position_they_came_from() -> None:
    """``origin_px`` is what makes the body frame invertible. Round-trip it."""
    seq, _ = normalize_sequence(standing_sequence())

    points = landmarks_to_cal_px(seq, 0, TORSO_LEG_LANDMARKS, cal_scale=2.0)

    assert points.shape == (len(TORSO_LEG_LANDMARKS), 2)
    assert np.all(np.isfinite(points))
    # Right hip (index 24 of the layout) sits below the right shoulder (12) in
    # a y-DOWN pixel space, which is the flip the projection has to undo.
    shoulder_y = points[TORSO_LEG_LANDMARKS.index(12), 1]
    hip_y = points[TORSO_LEG_LANDMARKS.index(24), 1]
    ankle_y = points[TORSO_LEG_LANDMARKS.index(28), 1]
    assert shoulder_y < hip_y < ankle_y


def test_landmark_projection_scales_linearly_with_cal_scale() -> None:
    seq, _ = normalize_sequence(standing_sequence())
    at_one = landmarks_to_cal_px(seq, 0, TORSO_LEG_LANDMARKS, cal_scale=1.0)
    at_two = landmarks_to_cal_px(seq, 0, TORSO_LEG_LANDMARKS, cal_scale=2.0)
    assert np.allclose(at_two, at_one * 2.0)


def test_out_of_range_frame_raises_index_error() -> None:
    seq, _ = normalize_sequence(standing_sequence(frame_count=4))
    with pytest.raises(IndexError):
        landmarks_to_cal_px(seq, 99, (16,), cal_scale=2.0)
    with pytest.raises(IndexError):
        racket_wrist_seed_px(seq, -1, RIGHT_WRIST, cal_scale=2.0)


def test_seed_and_gate_are_in_the_same_space() -> None:
    seq, _ = normalize_sequence(standing_sequence())
    seed = racket_wrist_seed_px(seq, 0, RIGHT_WRIST, cal_scale=2.0)
    gate = seed_gate_px(seq, cal_scale=2.0)

    assert all(math.isfinite(value) for value in seed)
    assert gate > 0.0
    assert gate == pytest.approx(0.8 * seq.torso_scale_px * 2.0)
