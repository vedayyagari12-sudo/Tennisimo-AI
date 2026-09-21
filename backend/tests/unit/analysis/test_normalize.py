"""Stage 7c unit tests (PIPELINE.md 4.5). Synthetic keypoints only."""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.normalize import (
    DEAD_TIME_OUTSIDE_CORE,
    LEFT_HIP,
    LEFT_SHOULDER,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    MIN_CORE_COVERAGE,
    RIGHT_WRIST,
    SUBJECT_IDENTITY_UNSTABLE,
    apply_aspect_correction,
    core_coverage,
    core_visibility,
    core_window_indices,
    flip_y,
    hold_unfillable_gaps,
    landmark_visible,
    longest_valid_run,
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
from tests.unit.analysis.synthetic import (
    NUM_LANDMARKS,
    make_pose_sequence,
    make_pose_sequence_with_valid_run,
    punch_hole,
)


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


def test_interpolation_still_refuses_gaps_longer_than_three_frames() -> None:
    """TIER 1, semantics unchanged (PIPELINE.md 7.1.2).

    A gap of at most three frames is interpolated; a longer one is NOT. The
    long-gap frames are edge-held and stay invalid, because a linear fill
    across them would fabricate the trajectory the product measures.
    """
    detected = np.ones(60, dtype=bool)
    detected[10:13] = False  # 3-frame gap: interpolated
    normalized, quality = normalize_sequence(make_pose_sequence(detected=detected))
    assert quality.longest_gap_frames == 3
    assert quality.interpolated_frames == 3
    assert not normalized.valid[11]
    # Interpolation recovered the linear sweep, so x stays monotone.
    assert np.all(np.diff(normalized.points[:, RIGHT_WRIST, 0]) > 0.0)

    detected = np.ones(60, dtype=bool)
    detected[20:40] = False  # 20-frame gap: never interpolated
    held, quality = normalize_sequence(make_pose_sequence(detected=detected))
    assert quality.longest_gap_frames == 20
    assert quality.interpolated_frames == 0
    assert not held.valid[20:40].any()
    # Frames deep inside the gap are a flat hold, not a ramp: had the gap been
    # interpolated, the fixture's linear sweep would keep marching here.
    inside = held.points[22:28, RIGHT_WRIST, 0]
    assert float(np.ptp(inside)) == pytest.approx(0.0, abs=1e-9)
    # ... whereas the frames around the gap are still moving.
    assert held.points[45, RIGHT_WRIST, 0] > held.points[15, RIGHT_WRIST, 0]


def test_hold_unfillable_gaps_leaves_short_gaps_to_the_interpolator() -> None:
    valid = np.ones(12, dtype=bool)
    valid[3:6] = False   # 3 frames: keep the interpolation
    valid[8:11] = False  # 3 frames: keep the interpolation
    ramp = np.arange(12, dtype=np.float64).reshape(12, 1)
    held, interpolated = hold_unfillable_gaps(ramp, valid, max_gap_frames=3)
    assert interpolated == 6
    assert np.allclose(held, ramp)

    valid = np.ones(12, dtype=bool)
    valid[3:9] = False  # 6 frames: hold, do not interpolate
    held, interpolated = hold_unfillable_gaps(ramp, valid, max_gap_frames=3)
    assert interpolated == 0
    assert np.allclose(held[3:6, 0], 2.0)  # first half holds the frame before
    assert np.allclose(held[6:9, 0], 9.0)  # second half holds the frame after


def test_a_long_gap_outside_the_core_window_does_not_make_the_clip_unusable() -> None:
    """TIER 2 (PIPELINE.md 7.1.2), the case that rejected a third of the corpus.

    Real footage: the player is untrackable before ~2.2 s and after ~4.7 s of an
    8 s analysis window. The whole-window ``longest_gap_frames`` is enormous and
    says nothing about whether the swing is measurable.
    """
    seq = make_pose_sequence_with_valid_run(
        clip_len_s=8.0, valid_run_start_s=2.2, valid_run_len_s=2.5
    )
    _, quality = normalize_sequence(seq)

    assert quality.usable is True
    assert quality.longest_gap_frames > 90  # the old gate rejected on exactly this
    assert quality.core_coverage_fraction == pytest.approx(1.0)
    assert quality.longest_core_gap_frames == 0
    assert DEAD_TIME_OUTSIDE_CORE in quality.flags
    # The 2 s core sits inside the valid run, around the swing at ~3.45 s.
    assert quality.core_window_start_s >= 2.2
    assert quality.core_window_end_s <= 4.7
    assert quality.core_window_end_s - quality.core_window_start_s == pytest.approx(
        2.0, abs=0.05
    )


def test_the_anchor_picks_the_longest_valid_run_when_there_are_two() -> None:
    seq = make_pose_sequence_with_valid_run(
        clip_len_s=10.0,
        valid_run_start_s=5.0,
        valid_run_len_s=4.0,
        swing_centre_s=6.5,
    )
    xy = np.asarray(seq.landmarks, dtype=np.float64)[:, :, :2]
    timestamps_s = np.asarray(seq.timestamps_s, dtype=np.float64)

    valid = np.zeros(300, dtype=bool)
    valid[10:70] = True    # 60 frames of decoy
    valid[150:270] = True  # 120 frames: the real run
    assert longest_valid_run(valid) == (150, 270)

    start, end = core_window_indices(valid, timestamps_s, xy)
    assert timestamps_s[end - 1] - timestamps_s[start] == pytest.approx(2.0, abs=0.05)
    assert 150 <= start and end <= 270
    # Refined within the run onto the swing at 6.5 s == frame 195.
    assert start <= 195 < end
    assert core_coverage(valid, (start, end)) == pytest.approx(1.0)


def test_the_core_window_clamps_at_the_sequence_ends() -> None:
    # Anchor near frame 0: the core may not run off the front of the array.
    seq = make_pose_sequence_with_valid_run(
        clip_len_s=3.0, valid_run_start_s=0.0, valid_run_len_s=1.0
    )
    valid = valid_frames(np.asarray(seq.landmarks, dtype=np.float64), seq.detected)
    start, end = core_window_indices(valid, seq.timestamps_s, seq.landmarks[:, :, :2])
    assert start == 0
    assert end == 60

    # A sequence shorter than the core window yields the whole sequence, and
    # the reported bounds say so rather than shrinking silently.
    short = make_pose_sequence(frame_count=30)
    _, quality = normalize_sequence(short)
    assert quality.core_window_start_s == pytest.approx(0.0)
    assert quality.core_window_end_s == pytest.approx(29.0 / 30.0)
    assert quality.core_coverage_fraction == pytest.approx(1.0)


def test_a_four_frame_hole_inside_the_core_makes_the_clip_unusable() -> None:
    """The rule that must still bite: fabrication is refused where it matters."""
    seq = make_pose_sequence_with_valid_run(
        clip_len_s=9.0,
        valid_run_start_s=2.0,
        valid_run_len_s=5.0,
        swing_centre_s=3.0,
    )
    holed = punch_hole(seq, 88, 92)  # ~2.93-3.07 s, straight through the swing
    _, quality = normalize_sequence(holed)

    assert quality.usable is False
    assert quality.longest_core_gap_frames == 4
    assert quality.core_coverage_fraction > MIN_CORE_COVERAGE  # coverage alone would pass


def test_the_same_hole_three_seconds_outside_the_core_is_tolerated_and_flagged() -> None:
    seq = make_pose_sequence_with_valid_run(
        clip_len_s=9.0,
        valid_run_start_s=2.0,
        valid_run_len_s=5.0,
        swing_centre_s=3.0,
    )
    holed = punch_hole(seq, 195, 199)  # 6.5 s, ~3 s past the core
    _, quality = normalize_sequence(holed)

    assert quality.usable is True
    assert quality.longest_core_gap_frames == 0
    assert quality.core_window_end_s < 6.5
    assert DEAD_TIME_OUTSIDE_CORE in quality.flags


def test_core_coverage_below_the_minimum_is_unusable() -> None:
    """Scattered 3-frame gaps, each individually legal under Tier 1.

    Coverage without contiguity is not the signal: no 2 s stretch of this clip
    is densely enough tracked to support a central-difference velocity.
    """
    seq = make_pose_sequence_with_valid_run(
        clip_len_s=8.0, valid_run_start_s=0.0, valid_run_len_s=8.0
    )
    for start in range(3, 235, 12):
        seq = punch_hole(seq, start, start + 3)
    _, quality = normalize_sequence(seq)

    assert quality.usable is False
    assert quality.longest_core_gap_frames <= 3  # every single gap is Tier-1 legal
    assert quality.core_coverage_fraction < MIN_CORE_COVERAGE


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
