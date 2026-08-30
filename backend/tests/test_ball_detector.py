"""Unit tests for app/ball/detector.py and app/ball/track.py (PIPELINE.md Stage 10).

Every frame in this file is SYNTHETIC: numpy arrays with a plain court-coloured
background and a drawn ball. No fixture video, no real footage, no network, and
no clock. All noise is seeded, so every assertion below is deterministic.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from app.ball.detector import (
    MOTION_DIFF_THRESHOLD,
    PLAYER_BOX_DILATION,
    build_background,
    candidate_mask,
    classify_contour,
    contour_features,
    detect_ball_track,
    find_candidates,
    torso_leg_exclusion_box,
)
from app.ball.track import (
    DIRECTION_CHANGE_LIMIT_DEG,
    EXIT_FRAME_MARGIN_PX,
    MAX_COASTED_FRAMES,
    MIN_ACCEPTED_DETECTIONS,
    STREAK_ORIENTATION_TOLERANCE_DEG,
    TERMINATED_COAST_EXHAUSTED,
    TERMINATED_DIRECTION_CHANGE,
    TERMINATED_WINDOW_END,
    TIER_REJECTED,
    TIER_ROUND,
    TIER_STREAK,
    axis_difference_deg,
    direction_difference_deg,
)
from app.models.enums import BallSpeedUnavailableReason

FRAME_HEIGHT: Final[int] = 360
FRAME_WIDTH: Final[int] = 640

#: A blue hard court. Fails both colour branches on its own.
COURT_BGR: Final[tuple[int, int, int]] = (120, 90, 70)
#: Optic yellow: HSV ~ (31, 208, 220), inside the yellow branch.
BALL_BGR: Final[tuple[int, int, int]] = (40, 220, 210)
#: A white court line. Passes the achromatic branch but is static.
LINE_BGR: Final[tuple[int, int, int]] = (235, 238, 240)

BALL_RADIUS_PX: Final[int] = 5
#: Well under MOTION_DIFF_THRESHOLD, so it must not survive the motion mask.
SENSOR_NOISE_SIGMA: Final[float] = 2.0

Position = tuple[int, int] | None


def _blank_frame(rng: np.random.Generator) -> npt.NDArray[np.uint8]:
    """A plain court background plus seeded sub-threshold sensor noise."""
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.float64)
    frame[:, :] = COURT_BGR
    frame += rng.normal(0.0, SENSOR_NOISE_SIGMA, frame.shape)
    return np.clip(frame, 0, 255).astype(np.uint8)


def make_round_ball_scene(
    positions: list[Position],
    *,
    radius: int = BALL_RADIUS_PX,
    seed: int = 1,
    static_line: bool = False,
) -> npt.NDArray[np.uint8]:
    """Render a (T, H, W, 3) BGR stack with a round ball at each position.

    A ``None`` position renders a ball-free frame.
    """
    rng = np.random.default_rng(seed)
    frames: list[npt.NDArray[np.uint8]] = []
    for position in positions:
        frame = _blank_frame(rng)
        if static_line:
            cv2.line(frame, (0, 300), (FRAME_WIDTH - 1, 300), LINE_BGR, 4)
        if position is not None:
            cv2.circle(frame, position, radius, BALL_BGR, -1)
        frames.append(frame)
    return np.stack(frames, axis=0)


def make_streak_scene(
    positions: list[tuple[int, int]],
    *,
    length: int = 30,
    thickness: int = 7,
    seed: int = 2,
) -> npt.NDArray[np.uint8]:
    """Render a motion-blur capsule (PIPELINE.md 10.2) at each start position.

    Each streak runs horizontally from ``position`` to ``position + length``, so
    its major axis is aligned with the ball travel direction.
    """
    rng = np.random.default_rng(seed)
    frames: list[npt.NDArray[np.uint8]] = []
    for x, y in positions:
        frame = _blank_frame(rng)
        cv2.line(frame, (x, y), (x + length, y), BALL_BGR, thickness)
        frames.append(frame)
    return np.stack(frames, axis=0)


def _only_contour_features(mask: npt.NDArray[np.uint8]):  # type: ignore[no-untyped-def]
    """Features of the single contour in a binary mask."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    assert len(contours) == 1
    features = contour_features(contours[0])
    assert features is not None
    return features


# --------------------------------------------------------------------------- #
# Background model and masking (PIPELINE.md 10.3)
# --------------------------------------------------------------------------- #


def test_median_background_excludes_the_moving_ball() -> None:
    frames = make_round_ball_scene([(100 + 40 * t, 180) for t in range(8)])
    background = build_background(frames)

    court_gray = cv2.cvtColor(
        np.full((1, 1, 3), COURT_BGR, dtype=np.uint8), cv2.COLOR_BGR2GRAY
    )[0, 0]
    # Every pixel the ball ever occupied still reads as court in the median.
    for t in range(8):
        assert abs(int(background[180, 100 + 40 * t]) - int(court_gray)) <= 3


def test_static_bright_line_fails_the_motion_mask() -> None:
    """The achromatic branch matches a white line; the motion mask kills it."""
    frames = make_round_ball_scene(
        [(100 + 40 * t, 180) for t in range(6)], static_line=True
    )
    background = build_background(frames)
    mask = candidate_mask(frames[0], background)

    assert not mask[298:303, :].any()  # the static line contributes nothing
    assert mask[175:186, 95:106].any()  # the moving ball does


def test_sub_threshold_noise_produces_no_candidates() -> None:
    frames = make_round_ball_scene([None] * 6, seed=99)
    background = build_background(frames)
    assert SENSOR_NOISE_SIGMA * 3 < MOTION_DIFF_THRESHOLD
    assert find_candidates(frames[0], background, 0) == []


# --------------------------------------------------------------------------- #
# Two-tier contour filter (PIPELINE.md 10.4)
# --------------------------------------------------------------------------- #


def test_round_blob_is_tier_a() -> None:
    mask = np.zeros((60, 60), dtype=np.uint8)
    cv2.circle(mask, (30, 30), BALL_RADIUS_PX, 255, -1)
    features = _only_contour_features(mask)

    assert classify_contour(features) == TIER_ROUND
    assert features.aspect == pytest.approx(1.0, abs=0.1)


def test_blur_streak_is_tier_b_and_would_fail_a_circularity_threshold() -> None:
    """PIPELINE.md 10.4: circularity alone would reject in-flight detections."""
    mask = np.zeros((80, 120), dtype=np.uint8)
    cv2.line(mask, (20, 40), (80, 40), 255, 7)
    features = _only_contour_features(mask)

    assert classify_contour(features) == TIER_STREAK
    assert features.circularity < 0.65  # would be rejected by the tier-A bound
    assert features.aspect > 1.6
    assert 3.0 <= features.minor_axis_px <= 20.0


def test_court_line_ghost_is_rejected_by_aspect_and_minor_axis() -> None:
    mask = np.zeros((80, 400), dtype=np.uint8)
    cv2.line(mask, (5, 40), (395, 40), 255, 2)
    features = _only_contour_features(mask)

    assert features.aspect > 12.0
    assert features.minor_axis_px < 3.0
    assert classify_contour(features) == TIER_REJECTED


def test_player_sized_blob_is_rejected_by_area() -> None:
    mask = np.zeros((300, 300), dtype=np.uint8)
    cv2.rectangle(mask, (50, 50), (200, 250), 255, -1)
    features = _only_contour_features(mask)

    assert features.area > 1500.0
    assert classify_contour(features) == TIER_REJECTED


def test_exclusion_box_covers_torso_and_legs_dilated_15_percent() -> None:
    points = np.array(
        [[100.0, 100.0], [200.0, 100.0], [100.0, 300.0], [200.0, 300.0]],
        dtype=np.float64,
    )
    x_min, y_min, x_max, y_max = torso_leg_exclusion_box(points)

    assert PLAYER_BOX_DILATION == 0.15
    assert (x_min, x_max) == pytest.approx((92.5, 207.5))
    assert (y_min, y_max) == pytest.approx((85.0, 315.0))


def test_candidate_inside_the_exclusion_box_is_discarded() -> None:
    frames = make_round_ball_scene([(100 + 20 * t, 180) for t in range(6)])
    background = build_background(frames)

    assert len(find_candidates(frames[2], background, 2)) == 1
    assert find_candidates(frames[2], background, 2, (100.0, 150.0, 300.0, 220.0)) == []


# --------------------------------------------------------------------------- #
# Angle helpers (PIPELINE.md 10.4 / 10.5)
# --------------------------------------------------------------------------- #


def test_direction_difference_is_folded_to_180() -> None:
    assert direction_difference_deg(10.0, -10.0) == pytest.approx(20.0)
    assert direction_difference_deg(179.0, -179.0) == pytest.approx(2.0)
    assert direction_difference_deg(0.0, 90.0) == pytest.approx(90.0)


def test_axis_difference_is_undirected() -> None:
    """A streak axis has no head or tail: 0 deg and 180 deg agree."""
    assert axis_difference_deg(0.0, 180.0) == pytest.approx(0.0)
    assert axis_difference_deg(10.0, -170.0) == pytest.approx(0.0)
    assert axis_difference_deg(80.0, 0.0) == pytest.approx(80.0)


# --------------------------------------------------------------------------- #
# End-to-end detection (PIPELINE.md 10.5)
# --------------------------------------------------------------------------- #


def test_clean_ball_is_tracked_across_frames_near_ground_truth() -> None:
    truth = [(100 + 20 * t, 180) for t in range(8)]
    result = detect_ball_track(make_round_ball_scene(truth))

    assert result.reason is None
    track = result.track
    assert track is not None
    assert track.xy_px.shape == (8, 2)
    assert track.terminated_by == TERMINATED_WINDOW_END
    assert track.coasted_frames == 0
    assert track.frame_offsets.tolist() == list(range(8))
    assert set(track.tier.tolist()) == {TIER_ROUND}
    assert np.allclose(track.xy_px, np.array(truth, dtype=np.float32), atol=1.0)


def test_no_ball_present_yields_no_track_and_the_no_seed_reason() -> None:
    result = detect_ball_track(make_round_ball_scene([None] * 8, seed=42))

    assert result.track is None
    # PIPELINE.md 10.5 step 1: no candidate to seed on -> no_track_seeded.
    assert result.reason is BallSpeedUnavailableReason.NO_TRACK_SEEDED


def test_implausible_jump_terminates_the_track_instead_of_absorbing_it() -> None:
    straight = [(100 + 20 * t, 180) for t in range(5)]
    # Inside the kinematic gate, but a ~90 deg turn: rejected, track terminates.
    turned = [(180, 225), (180, 265)]
    result = detect_ball_track(make_round_ball_scene(straight + turned, seed=3))

    assert result.reason is None
    track = result.track
    assert track is not None
    assert track.terminated_by == TERMINATED_DIRECTION_CHANGE
    assert track.xy_px.shape == (5, 2)
    assert np.allclose(track.xy_px, np.array(straight, dtype=np.float32), atol=1.0)
    # The post-turn detections were never absorbed.
    assert float(track.xy_px[:, 1].max()) < 200.0
    assert direction_difference_deg(90.0, 0.0) > DIRECTION_CHANGE_LIMIT_DEG


def test_blur_streaks_are_tracked_through_tier_b() -> None:
    starts = [(100 + 40 * t, 180) for t in range(7)]
    result = detect_ball_track(make_streak_scene(starts))

    assert result.reason is None
    track = result.track
    assert track is not None
    assert set(track.tier.tolist()) == {TIER_STREAK}
    assert track.xy_px.shape == (7, 2)
    # Centroid of a horizontal capsule sits at its midpoint.
    expected_x = np.array([x + 15.0 for x, _ in starts], dtype=np.float32)
    assert np.allclose(track.xy_px[:, 0], expected_x, atol=1.0)
    assert np.all(track.minor_axis_px >= 3.0)


def test_streak_across_the_travel_direction_is_rejected_by_orientation() -> None:
    """A streak whose axis disagrees with the last step direction is not
    associated (PIPELINE.md 10.4 orientation agreement)."""
    rng = np.random.default_rng(17)
    frames = []
    for index in range(6):
        frame = _blank_frame(rng)
        x = 100 + 40 * index
        if index < 4:
            cv2.line(frame, (x, 180), (x + 30, 180), BALL_BGR, 7)
        else:  # same place a real ball would be, but a vertical streak
            cv2.line(frame, (x, 165), (x, 195), BALL_BGR, 7)
        frames.append(frame)
    result = detect_ball_track(np.stack(frames, axis=0))

    assert result.reason is None
    track = result.track
    assert track is not None
    assert track.xy_px.shape[0] == 4
    # The two vertical streaks sit inside the kinematic gate and are valid tier-B
    # contours; only the orientation gate turns them into misses.
    assert track.coasted_frames == 2
    assert axis_difference_deg(90.0, 0.0) > STREAK_ORIENTATION_TOLERANCE_DEG


def test_detection_near_the_frame_edge_is_trimmed() -> None:
    inside = [(480, 180), (520, 180), (560, 180), (600, 180)]
    edge_x = FRAME_WIDTH - 6
    assert edge_x > FRAME_WIDTH - 1 - EXIT_FRAME_MARGIN_PX
    result = detect_ball_track(
        make_round_ball_scene([*inside, (edge_x, 180)], seed=5)
    )

    assert result.reason is None
    track = result.track
    assert track is not None
    assert track.xy_px.shape == (4, 2)
    assert float(track.xy_px[:, 0].max()) == pytest.approx(600.0, abs=1.0)
    # The edge ball is a valid contour; the trim turns it into a miss.
    assert track.coasted_frames == 1


def test_third_consecutive_miss_terminates_the_track() -> None:
    positions: list[Position] = [
        (100, 180),
        (120, 180),
        (140, 180),
        None,
        None,
        None,
        (220, 180),
    ]
    result = detect_ball_track(make_round_ball_scene(positions, seed=8))

    assert result.reason is None
    track = result.track
    assert track is not None
    assert track.terminated_by == TERMINATED_COAST_EXHAUSTED
    assert track.coasted_frames == MAX_COASTED_FRAMES
    assert track.xy_px.shape == (3, 2)


def test_fewer_than_three_detections_yields_no_track() -> None:
    positions: list[Position] = [(100, 180), (120, 180), None, None, None]
    result = detect_ball_track(make_round_ball_scene(positions, seed=6))

    assert MIN_ACCEPTED_DETECTIONS == 3
    assert result.track is None
    assert result.reason is BallSpeedUnavailableReason.TOO_FEW_DETECTIONS


def test_ball_hidden_by_the_exclusion_box_is_never_seeded() -> None:
    frames = make_round_ball_scene([(100 + 20 * t, 180) for t in range(6)])
    boxes: list[tuple[float, float, float, float] | None] = [
        (50.0, 150.0, 400.0, 220.0)
    ] * 6
    result = detect_ball_track(frames, exclusion_boxes=boxes)

    assert result.track is None
    assert result.reason is BallSpeedUnavailableReason.NO_TRACK_SEEDED


def test_separate_pre_roll_background_stack_is_accepted() -> None:
    truth = [(100 + 20 * t, 180) for t in range(6)]
    pre_roll = make_round_ball_scene([None] * 15, seed=21)
    result = detect_ball_track(make_round_ball_scene(truth), background_frames=pre_roll)

    assert result.reason is None
    track = result.track
    assert track is not None
    assert np.allclose(track.xy_px, np.array(truth, dtype=np.float32), atol=1.0)


def test_seed_gate_rejects_a_candidate_far_from_the_racket_hand() -> None:
    frames = make_round_ball_scene([(100 + 20 * t, 180) for t in range(6)])

    near = detect_ball_track(frames, seed_xy=(105.0, 182.0), seed_gate_px=40.0)
    far = detect_ball_track(frames, seed_xy=(500.0, 60.0), seed_gate_px=40.0)

    assert near.track is not None
    assert far.track is None
    assert far.reason is BallSpeedUnavailableReason.NO_TRACK_SEEDED


# --------------------------------------------------------------------------- #
# Input contract
# --------------------------------------------------------------------------- #


def test_empty_stack_returns_no_track_rather_than_raising() -> None:
    frames = np.zeros((0, FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    result = detect_ball_track(frames)

    assert result.track is None
    assert result.reason is BallSpeedUnavailableReason.NO_TRACK_SEEDED


@pytest.mark.parametrize(
    "frames",
    [
        np.zeros((4, FRAME_HEIGHT, FRAME_WIDTH), dtype=np.uint8),
        np.zeros((4, FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.float32),
    ],
)
def test_malformed_frame_stack_is_rejected(frames: npt.NDArray[np.generic]) -> None:
    with pytest.raises(ValueError):
        detect_ball_track(frames)  # type: ignore[arg-type]


def test_exclusion_boxes_must_match_the_frame_count() -> None:
    frames = make_round_ball_scene([(100, 180), (120, 180)])
    with pytest.raises(ValueError):
        detect_ball_track(frames, exclusion_boxes=[None])
