"""Stage 7c unit tests (PIPELINE.md 4.5). Synthetic keypoints only."""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.normalize import (
    LEFT_HIP,
    LEFT_SHOULDER,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    SUBJECT_IDENTITY_UNSTABLE,
    apply_aspect_correction,
    core_visibility,
    flip_y,
    landmark_visible,
    mid_hip,
    normalize_sequence,
    path_length,
    subject_identity_unstable,
    swing_direction_sign,
    to_body_frame,
    torso_lengths,
    torso_scale,
    valid_frames,
)
from app.models.enums import Handedness
from tests.unit.analysis.synthetic import NUM_LANDMARKS, make_pose_sequence


def skeleton(frame_count: int, torso_length: float = 0.25) -> np.ndarray:
    """(T, 33, 2) skeleton with a fixed torso length, hips at y=0.6."""
    xy = np.zeros((frame_count, NUM_LANDMARKS, 2), dtype=np.float64)
    xy[:, LEFT_HIP] = (0.45, 0.6)
    xy[:, RIGHT_HIP] = (0.55, 0.6)
    xy[:, LEFT_SHOULDER] = (0.45, 0.6 - torso_length)
    xy[:, RIGHT_SHOULDER] = (0.55, 0.6 - torso_length)
    return xy


def test_apply_aspect_correction_fixes_a_45_degree_segment_on_a_9_16_clip() -> None:
    # A segment that is 45 degrees in PIXELS: dy = 100 px, dx = 100 px on a
    # 360x640 frame means dx_norm = 100/360, dy_norm = 100/640.
    segment = np.array([[[0.0, 0.0], [100.0 / 360.0, 100.0 / 640.0]]])

    uncorrected = segment[0, 1] - segment[0, 0]
    angle_uncorrected = np.degrees(np.arctan2(uncorrected[1], uncorrected[0]))
    assert abs(angle_uncorrected - 45.0) > 10.0

    corrected = apply_aspect_correction(segment, 360, 640)
    delta = corrected[0, 1] - corrected[0, 0]
    assert np.degrees(np.arctan2(delta[1], delta[0])) == 45.0


def test_flip_y_makes_up_positive() -> None:
    flipped = flip_y(np.array([[[0.3, 0.8]]]))
    assert flipped[0, 0, 1] == -0.8
    assert flipped[0, 0, 0] == 0.3


def test_to_body_frame_is_translation_invariant() -> None:
    base = skeleton(5)
    shifted = base + np.array([0.21, -0.37])
    # Identical to within float64 round-off on the shift itself; the camera pan
    # is gone entirely.
    assert np.allclose(
        to_body_frame(base, mid_hip(base)),
        to_body_frame(shifted, mid_hip(shifted)),
        atol=1e-15,
    )
    assert np.allclose(mid_hip(to_body_frame(base, mid_hip(base))), 0.0)


def test_torso_scale_is_the_median_and_ignores_per_frame_foreshortening() -> None:
    xy = skeleton(9, torso_length=0.25)
    assert torso_scale(xy) == 0.25

    # Foreshorten a minority of frames hard: the median is unmoved, whereas a
    # per-frame scale would inject that foreshortening into every velocity.
    foreshortened = xy.copy()
    foreshortened[[0, 1, 7, 8], LEFT_SHOULDER, 1] = 0.6 - 0.05
    foreshortened[[0, 1, 7, 8], RIGHT_SHOULDER, 1] = 0.6 - 0.05
    assert torso_scale(foreshortened) == 0.25
    assert not np.allclose(torso_lengths(foreshortened), 0.25)

    assert torso_scale(np.zeros((3, NUM_LANDMARKS, 2))) == 0.0


def test_torso_scale_uses_only_valid_frames() -> None:
    xy = skeleton(6, torso_length=0.25)
    xy[4:, LEFT_SHOULDER, 1] = 0.6 - 1.0
    xy[4:, RIGHT_SHOULDER, 1] = 0.6 - 1.0
    valid = np.array([True, True, True, True, False, False])
    assert torso_scale(xy, valid) == 0.25


def test_core_visibility_and_valid_frames_gate_at_half() -> None:
    landmarks = np.zeros((3, NUM_LANDMARKS, 4))
    landmarks[0, :, 3] = 0.9
    landmarks[1, :, 3] = 0.5
    landmarks[2, :, 3] = 0.49
    assert np.allclose(core_visibility(landmarks), [0.9, 0.5, 0.49])

    detected = np.array([True, True, True])
    assert list(valid_frames(landmarks, detected)) == [True, True, False]
    # Undetected frames are invalid regardless of visibility.
    assert list(valid_frames(landmarks, np.array([False, True, True]))) == [
        False,
        True,
        False,
    ]


def test_subject_identity_unstable_fires_on_an_injected_subject_swap() -> None:
    xy = skeleton(10, torso_length=0.25)
    valid = np.ones(10, dtype=bool)
    assert not subject_identity_unstable(xy, valid, 0.25)

    # Mid-clip swap onto a different person: the hips jump sideways and the
    # torso gets shorter.
    swapped = xy.copy()
    swapped[5:, [LEFT_HIP, RIGHT_HIP], 0] += 0.2
    swapped[5:, [LEFT_SHOULDER, RIGHT_SHOULDER], 0] += 0.2
    swapped[5:, [LEFT_SHOULDER, RIGHT_SHOULDER], 1] = 0.6 - 0.12
    assert subject_identity_unstable(swapped, valid, 0.25)

    # Torso-length change alone is enough (40 % shrink, no centroid jump).
    shrunk = xy.copy()
    shrunk[5:, [LEFT_SHOULDER, RIGHT_SHOULDER], 1] = 0.6 - 0.15
    assert subject_identity_unstable(shrunk, valid, 0.25)


def test_path_length_of_a_unit_square_is_four() -> None:
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]])
    assert path_length(square) == 4.0
    assert path_length(np.zeros((1, 2))) == 0.0


def test_landmark_visible_is_a_per_landmark_threshold() -> None:
    visibility = np.array([0.9, 0.49, 0.5, 0.0])
    assert landmark_visible(visibility).tolist() == [True, False, True, False]
    assert landmark_visible(np.zeros((0,))).shape == (0,)


def test_path_length_excludes_steps_where_the_landmark_was_occluded() -> None:
    # A stationary landmark with one occluded frame that MediaPipe placed 5 TU
    # away: the unguarded sum charges the fictional round trip, 10 TU of path
    # that never happened.
    points = np.zeros((5, 2))
    points[2] = (5.0, 0.0)
    visibility = np.array([0.9, 0.9, 0.05, 0.9, 0.9])
    assert path_length(points) == pytest.approx(10.0)
    assert path_length(points, visibility) == pytest.approx(0.0)


def test_path_length_with_visibility_keeps_every_well_observed_step() -> None:
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]])
    assert path_length(square, np.full(5, 0.9)) == pytest.approx(4.0)
    assert path_length(square, np.zeros(5)) == 0.0


def test_swing_direction_sign_follows_the_wrist() -> None:
    timestamps = np.arange(20) / 30.0
    # Hand pauses, then sweeps in +x: sign is +1.
    forward_x = np.concatenate([np.zeros(5), np.linspace(0.0, 2.0, 15)])
    forward = np.stack([forward_x, np.zeros(20)], axis=1)
    assert swing_direction_sign(forward, timestamps) == 1
    assert swing_direction_sign(forward * np.array([-1.0, 1.0]), timestamps) == -1
    assert swing_direction_sign(np.zeros((1, 2)), timestamps[:1]) == 1


def test_normalize_sequence_end_to_end_on_a_clean_clip() -> None:
    seq = make_pose_sequence(frame_count=60, torso_px_fraction=0.25)
    normalized, quality = normalize_sequence(seq)

    assert quality.usable is True
    assert quality.flags == []
    assert quality.detection_rate == 1.0
    assert quality.longest_gap_frames == 0
    assert quality.interpolated_frames == 0
    assert quality.frames_with_pose == 60

    # Mid-hip is the origin and the torso is exactly one TU.
    assert np.allclose(mid_hip(normalized.points), 0.0, atol=1e-9)
    assert np.allclose(torso_lengths(normalized.points), 1.0, atol=1e-9)
    # y is up: shoulders sit above the hips.
    assert normalized.points[0, LEFT_SHOULDER, 1] > 0.0
    # The wrist sweeps in +x, so the sign is +1, and speeds are TU/s.
    assert normalized.swing_direction_sign == 1
    assert normalized.velocity[30, RIGHT_WRIST, 0] > 0.0
    assert quality.torso_scale_px == pytest.approx(0.25 * 640, rel=1e-6)

    expected_dx_per_frame = 0.6 / 59 * (360.0 / 640.0) / 0.25
    assert np.isclose(
        normalized.velocity[30, RIGHT_WRIST, 0], expected_dx_per_frame * 30.0
    )


def test_normalize_sequence_interpolates_short_gaps_and_rejects_long_ones() -> None:
    detected = np.ones(60, dtype=bool)
    detected[10:13] = False  # 3-frame gap: allowed
    normalized, quality = normalize_sequence(make_pose_sequence(detected=detected))
    assert quality.usable is True
    assert quality.longest_gap_frames == 3
    assert quality.interpolated_frames == 3
    assert not normalized.valid[11]
    # Interpolation recovered the linear sweep, so x stays monotone.
    assert np.all(np.diff(normalized.points[:, RIGHT_WRIST, 0]) > 0.0)

    detected = np.ones(60, dtype=bool)
    detected[10:15] = False  # 5-frame gap: rejected
    _, bad = normalize_sequence(make_pose_sequence(detected=detected))
    assert bad.usable is False
    assert bad.longest_gap_frames == 5


def test_normalize_sequence_handles_degenerate_input_without_raising() -> None:
    empty = make_pose_sequence(frame_count=0)
    normalized, quality = normalize_sequence(empty)
    assert quality.usable is False
    assert normalized.points.shape[0] == 0

    undetected = make_pose_sequence(detected=np.zeros(60, dtype=bool))
    _, quality = normalize_sequence(undetected)
    assert quality.usable is False
    assert quality.detection_rate == 0.0
    assert quality.mean_visibility == 0.0


def test_normalize_sequence_flags_a_subject_swap_but_stays_usable() -> None:
    seq = make_pose_sequence(frame_count=60)
    landmarks = seq.landmarks.copy()
    landmarks[30:, [LEFT_HIP, RIGHT_HIP], 0] += 0.2
    landmarks[30:, [LEFT_SHOULDER, RIGHT_SHOULDER], 0] += 0.2
    landmarks[30:, [LEFT_SHOULDER, RIGHT_SHOULDER], 1] += 0.12
    swapped = type(seq)(
        landmarks=landmarks,
        world=seq.world,
        timestamps_s=seq.timestamps_s,
        detected=seq.detected,
        width_px=seq.width_px,
        height_px=seq.height_px,
    )
    _, quality = normalize_sequence(swapped)
    assert SUBJECT_IDENTITY_UNSTABLE in quality.flags
    assert quality.usable is True


def test_normalize_sequence_mirrors_for_a_left_handed_clip() -> None:
    seq = make_pose_sequence(handedness=Handedness.LEFT)
    normalized, quality = normalize_sequence(seq)
    assert quality.usable is True
    # The left wrist is the moving one; the sign still reads +1 because the
    # sweep is in +x.
    assert normalized.swing_direction_sign == 1
