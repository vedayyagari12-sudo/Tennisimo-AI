"""Stage 9 unit tests (PIPELINE.md Stage 9, 4.5). Synthetic data only."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.analysis.contact import (
    CLIP_EDGE_MARGIN_FRAMES,
    CONFIDENCE_FLOOR,
    MAX_SUSTAINED_RUN_FRAMES,
    MIN_SUSTAINED_RUN_FRAMES,
    MAX_HELD_FRACTION,
    MIN_APPROACH_COVERAGE,
    MOTION_NOT_SUSTAINED,
    RACKET_WRIST_UNOBSERVED,
    approach_motion_coverage,
    arm_extension_ratio,
    detect_contact_frame,
    find_deceleration_onset,
    forward_swing_window_start,
    held_landmark_fraction,
    motion_evidence_absent,
    observed_fraction,
    peak_prominence_ratio,
    SUSTAINED_SPEED_FRACTION,
    peak_speed_index,
    racket_hand_speed,
    racket_wrist_reliable,
    sustained_motion_frames,
)
from app.analysis.normalize import RIGHT_SHOULDER, RIGHT_WRIST, SUBJECT_IDENTITY_UNSTABLE
from app.analysis.smoothing import central_difference
from app.models.enums import Handedness, HandednessSource
from app.models.responses import HandednessResult
from tests.unit.analysis.synthetic import (
    arc_positions,
    make_normalized_sequence,
    make_pose_quality,
    triangular_speed,
)

RIGHT_HANDED = HandednessResult(
    handedness=Handedness.RIGHT, confidence=0.95, source=HandednessSource.DETECTED
)

FRAME_COUNT = 61
PEAK_FRAME = 30
PEAK_SPEED = 12.0


def clean_sequence_and_speed() -> tuple[object, np.ndarray]:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    return make_normalized_sequence(speed), speed


def double_peak_speed(
    frame_count: int = 70,
    *,
    toss_frame: int = 15,
    toss_speed: float = 7.0,
    hit_frame: int = 40,
    hit_speed: float = 12.0,
    width: float = 3.0,
) -> np.ndarray:
    """Serve: a toss peak, then a higher hit peak, well separated in time.

    A small shoulder bump three frames after the hit gives a naive
    well-separation rule something wrong to latch onto.
    """
    frames = np.arange(frame_count, dtype=np.float64)
    toss = toss_speed * np.exp(-(((frames - toss_frame) / width) ** 2))
    hit = hit_speed * np.exp(-(((frames - hit_frame) / width) ** 2))
    shoulder = 0.45 * hit_speed * np.exp(-(((frames - (hit_frame + 3)) / 0.9) ** 2))
    return 0.5 + toss + hit + shoulder


# --- component functions -----------------------------------------------------


def test_racket_hand_speed_reads_the_racket_wrist_in_tu_per_s() -> None:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    seq = make_normalized_sequence(speed)
    assert np.allclose(racket_hand_speed(seq, Handedness.RIGHT), speed)
    # The off hand is the slow one, so picking the wrong wrist is visible.
    assert racket_hand_speed(seq, Handedness.LEFT).max() < speed.max()


def sharp_peak_speed(
    frame_count: int = 40,
    *,
    peak_frame: int = 20,
    peak_speed: float = 14.0,
    baseline: float = 1.0,
) -> np.ndarray:
    """A one-frame spike that collapses immediately: the real-footage shape that
    the old percentage rule read 2 frames LATE.

    Measured on real clips the frame after the peak sits at 55-88 % of it and
    the frame after that has already fallen off a cliff. There is no plateau
    here, so the peak itself is the deceleration onset.
    """
    speed = np.full(frame_count, baseline, dtype=np.float64)
    speed[peak_frame - 2] = 0.40 * peak_speed
    speed[peak_frame - 1] = 0.55 * peak_speed
    speed[peak_frame] = peak_speed
    speed[peak_frame + 1] = 0.86 * peak_speed
    speed[peak_frame + 2] = 0.31 * peak_speed
    speed[peak_frame + 3] = 0.22 * peak_speed
    return speed


def plateau_peak_speed(
    frame_count: int = 40,
    *,
    peak_frame: int = 20,
    peak_speed: float = 14.0,
    baseline: float = 1.0,
) -> np.ndarray:
    """A spike sitting on top of a flat three-frame plateau, then a cliff.

    This is the other real-footage shape: the hand is still travelling at swing
    speed for three more frames and contact is at the END of that run, not at
    the first dip below the peak.
    """
    speed = np.full(frame_count, baseline, dtype=np.float64)
    speed[peak_frame - 2] = 0.39 * peak_speed
    speed[peak_frame - 1] = 0.57 * peak_speed
    speed[peak_frame] = peak_speed
    speed[peak_frame + 1] = 0.55 * peak_speed
    speed[peak_frame + 2] = 0.57 * peak_speed
    speed[peak_frame + 3] = 0.51 * peak_speed
    speed[peak_frame + 4] = 0.20 * peak_speed
    return speed


def test_find_deceleration_onset_holds_at_a_sharp_peak() -> None:
    """The early-bias shape: one elevated frame after the peak is not a plateau.

    The Stage 7 SG window of 5 smears any one-frame spike into its immediate
    neighbours, so speed[peak + 1] carries the peak's own energy. Advancing onto
    it is exactly the 2-frame late bias measured on real footage.
    """
    peak = 20
    speed = sharp_peak_speed(peak_frame=peak)
    assert find_deceleration_onset(speed, peak) == peak


def test_find_deceleration_onset_walks_to_the_end_of_a_plateau() -> None:
    """The late-bias shape: contact is the last sustained frame, not the first dip."""
    peak = 20
    speed = plateau_peak_speed(peak_frame=peak)
    onset = find_deceleration_onset(speed, peak)
    assert onset == peak + 3
    # Every frame walked over is still travelling at swing speed, and the one
    # after the answer is not.
    threshold = float(speed[peak]) * SUSTAINED_SPEED_FRACTION
    assert (speed[peak : onset + 1] >= threshold).all()
    assert speed[onset + 1] < threshold


def test_sharp_and_plateau_peaks_are_resolved_differently() -> None:
    """The two shapes must not collapse onto one rule.

    A single threshold on percentage-of-peak drop gives peak + 1 for both, which
    is 2 frames late on the plateau shape and 1 frame late on the sharp one.
    """
    peak = 20
    sharp = find_deceleration_onset(sharp_peak_speed(peak_frame=peak), peak)
    plateau = find_deceleration_onset(plateau_peak_speed(peak_frame=peak), peak)
    assert sharp == peak
    assert plateau > sharp + 1


def test_find_deceleration_onset_requires_the_plateau_to_reach_min_run() -> None:
    """A run shorter than MIN_SUSTAINED_RUN_FRAMES falls back to the peak."""
    peak = 5
    speed = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 8.0, 1.0, 1.0, 1.0])
    assert MIN_SUSTAINED_RUN_FRAMES == 2
    assert find_deceleration_onset(speed, peak) == peak
    # Lowering the requirement to one frame is what would take the answer to 6.
    assert find_deceleration_onset(speed, peak, min_run_frames=1) == peak + 1


def test_find_deceleration_onset_caps_a_broad_slowly_decaying_peak() -> None:
    """A gradual real deceleration must not drag the onset into the follow-through.

    A triangular profile stays inside the sustained band for 17 frames; without
    the cap the onset would land there, deep in the follow-through.
    """
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    onset = find_deceleration_onset(speed, PEAK_FRAME)
    assert onset == PEAK_FRAME + MAX_SUSTAINED_RUN_FRAMES
    uncapped = find_deceleration_onset(speed, PEAK_FRAME, max_run_frames=FRAME_COUNT)
    assert uncapped > onset + 10


def test_find_deceleration_onset_without_a_drop_returns_the_last_frame() -> None:
    rising = np.linspace(1.0, 10.0, 20)
    assert find_deceleration_onset(rising, int(np.argmax(rising))) == 19


def test_forward_swing_window_start_is_the_last_local_minimum_before_the_peak() -> None:
    forward = np.array([0.0, -0.3, -0.6, -0.4, 0.0, -0.5, 0.2, 0.9, 1.4, 1.2])
    assert forward_swing_window_start(forward, peak_index=8) == 5


def test_peak_prominence_ratio_uses_only_well_separated_peaks() -> None:
    speed = double_peak_speed()
    peak = int(np.argmax(speed))
    ratio = peak_prominence_ratio(speed, peak)
    assert 1.4 < ratio < 2.1
    # A naive rule that allows a neighbour of the peak to count as the rival
    # collapses to ~1.0 and would report a clean swing as ambiguous.
    naive = peak_prominence_ratio(speed, peak, min_separation_frames=1)
    assert naive < 1.35
    assert ratio > naive + 0.25


def test_peak_prominence_ratio_caps_when_there_is_no_rival_peak() -> None:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    assert peak_prominence_ratio(speed, PEAK_FRAME) == 10.0


# --- the clean case ----------------------------------------------------------


def test_clean_single_peak_detects_deceleration_onset_not_argmax() -> None:
    seq, speed = clean_sequence_and_speed()
    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())

    assert result.peak_frame_index == PEAK_FRAME
    assert result.frame_index != PEAK_FRAME, "argmax shortcut lands 1-2 frames early"
    assert result.frame_index == find_deceleration_onset(speed, PEAK_FRAME)
    assert result.frame_index - PEAK_FRAME == MAX_SUSTAINED_RUN_FRAMES
    assert result.sanity_flags == []
    assert result.confidence > 0.8
    assert result.peak_hand_speed_tu_s == PEAK_SPEED
    assert result.method == "peak_speed_decel_onset_v1"


def test_contact_absolute_time_carries_the_window_offset() -> None:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    seq = make_normalized_sequence(speed, start_time_s=8.0)
    result = detect_contact_frame(
        seq, RIGHT_HANDED, make_pose_quality(), analysis_window_start_s=8.0
    )
    assert result.time_s == pytest.approx(result.frame_index / 30.0)
    assert result.contact_absolute_time_s == pytest.approx(8.0 + result.time_s)
    assert result.contact_absolute_time_s != result.time_s


def test_left_handed_swing_is_measured_on_the_left_wrist() -> None:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    seq = make_normalized_sequence(speed, handedness=Handedness.LEFT)
    left = HandednessResult(
        handedness=Handedness.LEFT, confidence=0.9, source=HandednessSource.DETECTED
    )
    result = detect_contact_frame(seq, left, make_pose_quality())
    assert result.peak_frame_index == PEAK_FRAME
    assert result.peak_hand_speed_tu_s == PEAK_SPEED
    assert result.sanity_flags == []


# --- the serve / double-peak case -------------------------------------------


def test_double_peak_serve_picks_the_hit_and_reports_the_ambiguity() -> None:
    speed = double_peak_speed()
    seq = make_normalized_sequence(speed)
    clean_seq, clean_speed = clean_sequence_and_speed()

    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())
    clean = detect_contact_frame(clean_seq, RIGHT_HANDED, make_pose_quality())

    hit_frame = int(np.argmax(speed))
    assert result.peak_frame_index == hit_frame == 40
    assert result.frame_index >= hit_frame
    assert result.frame_index == find_deceleration_onset(speed, hit_frame)
    # The toss peak must not be mistaken for contact.
    assert abs(result.frame_index - 15) > 20

    # prominence_ratio reflects the ambiguity: ~hit/toss, not the cap.
    assert 1.4 < result.prominence_ratio < 2.1
    assert result.prominence_ratio < clean.prominence_ratio
    assert result.confidence < clean.confidence
    assert result.confidence > 0.35


# --- sanity gates, each in isolation ----------------------------------------


def test_gate_wrist_behind_mid_hip_lowers_confidence() -> None:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    baseline = detect_contact_frame(
        make_normalized_sequence(speed), RIGHT_HANDED, make_pose_quality()
    )
    # Same geometry, opposite declared swing direction: the wrist is now on the
    # wrong side of mid-hip at contact.
    behind = make_normalized_sequence(speed, swing_direction_sign=-1)
    behind = dataclasses.replace(
        behind, points=make_normalized_sequence(speed).points
    )
    result = detect_contact_frame(behind, RIGHT_HANDED, make_pose_quality())

    assert "wrist_behind_mid_hip" in result.sanity_flags
    assert result.confidence < baseline.confidence
    assert result.frame_index == baseline.frame_index


def test_gate_arm_not_extended_lowers_confidence() -> None:
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    baseline = detect_contact_frame(
        make_normalized_sequence(speed), RIGHT_HANDED, make_pose_quality()
    )
    # Extension peaks late in the follow-through instead of at contact, so the
    # contact frame sits low in the clip range of arm extension.
    flexed = arc_positions(FRAME_COUNT, FRAME_COUNT - 1, extension_width_frames=4.0)
    result = detect_contact_frame(
        make_normalized_sequence(speed, wrist_positions=flexed),
        RIGHT_HANDED,
        make_pose_quality(),
    )

    assert "arm_not_extended" in result.sanity_flags
    assert result.confidence < baseline.confidence


def test_gate_contact_near_clip_end_lowers_confidence() -> None:
    short_count = 14
    speed = triangular_speed(short_count, 11, PEAK_SPEED)
    seq = make_normalized_sequence(speed)
    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality(frames=short_count))

    assert result.frame_index > short_count - 1 - CLIP_EDGE_MARGIN_FRAMES
    assert "contact_near_clip_end" in result.sanity_flags
    assert result.confidence < 1.0


def test_gate_subject_identity_unstable_lowers_confidence() -> None:
    seq, _ = clean_sequence_and_speed()
    baseline = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())
    flagged = detect_contact_frame(
        seq, RIGHT_HANDED, make_pose_quality(flags=[SUBJECT_IDENTITY_UNSTABLE])
    )

    assert flagged.sanity_flags == [SUBJECT_IDENTITY_UNSTABLE]
    assert flagged.confidence < baseline.confidence
    assert flagged.frame_index == baseline.frame_index


# --- never raises ------------------------------------------------------------


def test_degenerate_inputs_return_a_result_instead_of_raising() -> None:
    empty = make_normalized_sequence(np.zeros((0,), dtype=np.float64))
    result = detect_contact_frame(empty, RIGHT_HANDED, make_pose_quality(frames=0))
    assert result.confidence == 0.0
    assert result.sanity_flags == ["sequence_unusable"]
    assert result.peak_hand_speed_tu_s is None

    two_frames = make_normalized_sequence(np.array([1.0, 2.0]))
    assert detect_contact_frame(two_frames, RIGHT_HANDED).confidence == 0.0

    zero_speed = make_normalized_sequence(np.zeros(30, dtype=np.float64))
    assert detect_contact_frame(zero_speed, RIGHT_HANDED, make_pose_quality()).confidence >= 0.0

    nan_seq = dataclasses.replace(
        make_normalized_sequence(triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)),
        velocity=np.full((FRAME_COUNT, 33, 2), np.nan),
    )
    assert 0.0 <= detect_contact_frame(nan_seq, RIGHT_HANDED).confidence <= 1.0

    unknown = HandednessResult(
        handedness=Handedness.UNKNOWN, confidence=0.1, source=HandednessSource.DETECTED
    )
    seq, _ = clean_sequence_and_speed()
    assert 0.0 <= detect_contact_frame(seq, unknown, make_pose_quality()).confidence <= 1.0


def test_degenerate_fallback_reports_peak_speed_as_none_not_zero() -> None:
    """Fewer than 3 usable frames means NO speed was measured.

    0.0 is a legitimate measured peak speed, so the failure path must report
    None; otherwise Stage 13 copies a fabricated "0.0 TU/s" into SwingMetrics
    and it becomes indistinguishable from a real measurement.
    """
    no_frames = make_normalized_sequence(np.zeros((0,), dtype=np.float64))
    empty_result = detect_contact_frame(no_frames, RIGHT_HANDED, make_pose_quality(frames=0))
    assert empty_result.sanity_flags == ["sequence_unusable"]
    assert empty_result.peak_hand_speed_tu_s is None

    two_frames = make_normalized_sequence(np.array([1.0, 2.0]))
    two_frame_result = detect_contact_frame(two_frames, RIGHT_HANDED)
    assert two_frame_result.sanity_flags == ["sequence_unusable"]
    assert two_frame_result.peak_hand_speed_tu_s is None


def stationary_sequence_with_occlusion_spike(
    *,
    frame_count: int = 61,
    glitch_frame: int = 30,
    jump_tu: float = 0.6,
    visible: bool = False,
):
    """A motionless player, plus ONE frame where the tracker lost the wrist.

    This is the real-footage failure in miniature: the player stands still with
    the racket at the hip, the racket wrist is occluded for a single frame, and
    MediaPipe puts it ``jump_tu`` away. The static sanity gates cannot see
    anything wrong -- the wrist is forward of mid-hip, and relative to this
    clip's own (tiny) range of arm extension the glitch frame looks fully
    extended -- so the only evidence that it is not a swing is the visibility
    channel and the absence of sustained motion.

    ``visible=True`` marks the glitch frame as well-seen, which is what the code
    used to assume about every frame; it is kept as the contrast case.
    """
    speed = np.zeros(frame_count, dtype=np.float64)
    seq = make_normalized_sequence(speed)

    points = seq.points.copy()
    shoulder = points[0, RIGHT_SHOULDER, :]
    points[:, RIGHT_WRIST, :] = shoulder + np.array([0.05, -0.55])
    points[glitch_frame, RIGHT_WRIST, :] = shoulder + np.array([jump_tu, -0.1])

    velocity = np.zeros_like(seq.velocity)
    velocity[:, RIGHT_WRIST, :] = central_difference(
        points[:, RIGHT_WRIST, :], seq.timestamps_s
    )

    visibility = seq.visibility.copy()
    if not visible:
        visibility[glitch_frame, RIGHT_WRIST] = 0.05

    return dataclasses.replace(
        seq, points=points, velocity=velocity, visibility=visibility
    )


def test_racket_wrist_reliable_needs_the_central_difference_neighbours() -> None:
    seq = stationary_sequence_with_occlusion_spike(glitch_frame=10)
    reliable = racket_wrist_reliable(seq, Handedness.RIGHT)
    assert not reliable[9] and not reliable[10] and not reliable[11]
    assert reliable[8] and reliable[12]


def test_peak_speed_index_ignores_unobserved_frames() -> None:
    speed = np.array([1.0, 2.0, 9.0, 2.0, 3.0])
    reliable = np.array([True, True, False, True, True])
    assert peak_speed_index(speed) == 2
    assert peak_speed_index(speed, reliable) == 4
    # Nothing observable at all: still returns a frame rather than failing.
    assert peak_speed_index(speed, np.zeros(5, dtype=bool)) == 2


def test_observed_fraction_is_the_share_of_trustworthy_frames() -> None:
    assert observed_fraction(np.array([True, True, False, False])) == pytest.approx(0.5)
    assert observed_fraction(np.zeros((0,), dtype=bool)) == 0.0


def test_sustained_motion_frames_counts_the_moving_run() -> None:
    speed = np.array([0.0, 1.0, 8.0, 10.0, 7.0, 0.5, 0.0])
    assert sustained_motion_frames(speed, 3, 10.0) == 3
    # An isolated single frame of speed is not sustained motion.
    isolated = np.array([0.0, 0.0, 10.0, 0.0, 0.0])
    assert sustained_motion_frames(isolated, 2, 10.0) == 1
    # A run the tracker could not see does not count at all.
    unreliable = np.array([True, True, False, False, False, True, True])
    assert sustained_motion_frames(speed, 3, 10.0, reliable=unreliable) == 0


def test_peak_prominence_ratio_ignores_unobserved_rivals() -> None:
    speed = np.zeros(40)
    speed[10] = 12.0   # the real swing
    speed[30] = 20.0   # a tracking glitch
    reliable = np.ones(40, dtype=bool)
    reliable[29:32] = False
    assert peak_prominence_ratio(speed, 10) < 1.0
    assert peak_prominence_ratio(speed, 10, reliable=reliable) == pytest.approx(10.0)


def test_arm_extension_ratio_ignores_occluded_frames() -> None:
    seq = stationary_sequence_with_occlusion_spike(glitch_frame=30)
    # The glitch frame is by far the most "extended" pose in the clip, and it is
    # the frame nobody saw.
    assert arm_extension_ratio(seq, Handedness.RIGHT, 30) == 0.0
    # With the outlier out of the range, the observed frames are all identical.
    assert arm_extension_ratio(seq, Handedness.RIGHT, 5) == 1.0


def test_stationary_player_with_an_occlusion_spike_is_not_a_confident_contact() -> None:
    """Bug 2, pinned: a motionless player must not outscore a real swing."""
    seq = stationary_sequence_with_occlusion_spike(glitch_frame=30)
    speed = racket_hand_speed(seq, Handedness.RIGHT)
    # The bug's trigger: the glitch is the global speed maximum of the clip.
    assert int(np.argmax(speed)) in (29, 30, 31)

    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())
    assert result.peak_frame_index not in (29, 30, 31)
    assert result.confidence < CONFIDENCE_FLOOR


def test_an_isolated_speed_spike_trips_the_motion_presence_gate() -> None:
    """Same spike, but the tracker claims it saw the wrist: motion is still absent."""
    seq = stationary_sequence_with_occlusion_spike(glitch_frame=30, visible=True)
    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())
    assert MOTION_NOT_SUSTAINED in result.sanity_flags
    assert result.confidence < CONFIDENCE_FLOOR


def test_a_real_swing_does_not_trip_the_motion_presence_gate() -> None:
    seq, _ = clean_sequence_and_speed()
    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())
    assert MOTION_NOT_SUSTAINED not in result.sanity_flags
    assert result.confidence > CONFIDENCE_FLOOR


def test_a_never_observed_racket_wrist_yields_no_confidence() -> None:
    seq = stationary_sequence_with_occlusion_spike(glitch_frame=30)
    visibility = seq.visibility.copy()
    visibility[:, RIGHT_WRIST] = 0.05
    seq = dataclasses.replace(seq, visibility=visibility)

    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())
    assert RACKET_WRIST_UNOBSERVED in result.sanity_flags
    assert result.confidence == 0.0


# --- motion-presence evidence (Stage 9 step 5b) ------------------------------


def trophy_hold_speed(
    frame_count: int = 120,
    *,
    spike_frame: int = 60,
    spike_speed: float = 10.0,
    baseline: float = 0.0,
) -> np.ndarray:
    """A long motionless hold, then a brief fast excursion, then stillness again.

    The real-footage shape this gate exists for: the player freezes in the
    trophy position (or the tracker holds a landmark across a gap), and a short
    burst of speed appears out of nothing. The burst is three frames wide, so it
    clears the peak-relative run test by construction -- a 3-frame event is
    trivially above half of its own maximum for 3 frames.
    """
    speed = np.full(frame_count, float(baseline), dtype=np.float64)
    speed[spike_frame - 1] = spike_speed * 0.7
    speed[spike_frame] = spike_speed
    speed[spike_frame + 1] = spike_speed * 0.6
    return speed


def test_approach_motion_coverage_separates_a_ramp_from_a_dead_stop() -> None:
    ramp = np.array([2.0, 4.0, 6.0, 8.0, 9.0, 9.5, 10.0])
    assert approach_motion_coverage(ramp, 6, 10.0, window=6) == pytest.approx(1.0)

    # Same peak, same run length, but the hand was at rest going into it.
    dead_stop = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 7.0, 10.0])
    assert approach_motion_coverage(dead_stop, 6, 10.0, window=6) == pytest.approx(1 / 6)

    # Frames the tracker could not see are not evidence of an approach.
    unreliable = np.zeros(7, dtype=bool)
    assert approach_motion_coverage(ramp, 6, 10.0, window=6, reliable=unreliable) == 0.0

    # Nothing precedes frame 0, and an empty clip does not raise.
    assert approach_motion_coverage(ramp, 0, 10.0) == 0.0
    assert approach_motion_coverage(np.zeros((0,), dtype=np.float64), 0, 10.0) == 0.0


def test_held_landmark_fraction_counts_repeated_positions() -> None:
    # Exactly-zero speed means the coordinate was repeated, not measured.
    held = np.concatenate([np.zeros(20), np.linspace(1.0, 10.0, 20)])
    assert held_landmark_fraction(held, 25, window=40) == pytest.approx(0.5)

    # A tracked landmark on real video jitters: small, but never exactly zero.
    jitter = np.full(40, 0.01)
    assert held_landmark_fraction(jitter, 25, window=40) == 0.0

    # Untrustworthy frames are observed_fraction's business, not this one's.
    reliable = np.ones(40, dtype=bool)
    reliable[:20] = False
    assert held_landmark_fraction(held, 25, window=40, reliable=reliable) == 0.0

    # Only the neighbourhood counts, and degenerate input does not raise.
    assert held_landmark_fraction(held, 35, window=4) == 0.0
    assert held_landmark_fraction(np.zeros((0,), dtype=np.float64), 0) == 0.0


def test_motion_evidence_absent_accepts_a_swing_and_rejects_its_imitations() -> None:
    swing = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    assert motion_evidence_absent(swing, PEAK_FRAME, PEAK_SPEED) is False

    # 1. a single isolated frame of speed
    isolated = np.full(FRAME_COUNT, 0.5)
    isolated[PEAK_FRAME] = PEAK_SPEED
    assert motion_evidence_absent(isolated, PEAK_FRAME, PEAK_SPEED) is True

    # 2. a multi-frame burst out of a dead stop -- clears the run test, which is
    #    the failure this fix is about, and fails the approach test.
    burst = trophy_hold_speed(FRAME_COUNT, spike_frame=PEAK_FRAME, spike_speed=PEAK_SPEED,
                              baseline=0.02)
    assert sustained_motion_frames(burst, PEAK_FRAME, PEAK_SPEED) >= 2
    assert held_landmark_fraction(burst, PEAK_FRAME) == 0.0
    assert motion_evidence_absent(burst, PEAK_FRAME, PEAK_SPEED) is True

    # 3. a clean ramp whose surrounding landmark stream was held, not tracked
    stream = np.full(120, 0.5)
    stream[20:60] = 0.0
    stream[60:67] = np.linspace(1.0, PEAK_SPEED, 7)
    assert approach_motion_coverage(stream, 66, PEAK_SPEED) >= MIN_APPROACH_COVERAGE
    assert held_landmark_fraction(stream, 66) > MAX_HELD_FRACTION
    assert motion_evidence_absent(stream, 66, PEAK_SPEED) is True


def test_motion_evidence_is_silent_exactly_on_the_thresholds() -> None:
    """A clip sitting on a threshold passes: firing on a good swing costs more."""
    speed = np.full(120, 0.5)
    speed[20:28] = 0.0                      # 8 held frames in an 81-frame neighbourhood
    speed[54:61] = np.linspace(1.0, 10.0, 7)
    assert held_landmark_fraction(speed, 60) <= MAX_HELD_FRACTION
    assert approach_motion_coverage(speed, 60, 10.0) >= MIN_APPROACH_COVERAGE
    assert motion_evidence_absent(speed, 60, 10.0) is False


def test_a_trophy_hold_followed_by_a_burst_trips_the_motion_gate() -> None:
    """The batch failure in miniature: static hold, brief spike, every other gate clear."""
    speed = trophy_hold_speed()
    seq = make_normalized_sequence(speed)
    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality(frames=120))

    assert sustained_motion_frames(speed, result.peak_frame_index, PEAK_SPEED) >= 2
    assert MOTION_NOT_SUSTAINED in result.sanity_flags
    assert result.confidence < CONFIDENCE_FLOOR


def test_a_ramped_swing_on_a_live_stream_keeps_its_confidence() -> None:
    """The control direction: a good detection must not be penalised."""
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, PEAK_SPEED)
    seq = make_normalized_sequence(speed)
    result = detect_contact_frame(seq, RIGHT_HANDED, make_pose_quality())

    assert MOTION_NOT_SUSTAINED not in result.sanity_flags
    assert result.confidence >= CONFIDENCE_FLOOR
