"""Session Mode swing segmentation (SESSION_MODE.md 4.5).

Synthetic sessions prove the logic, not the accuracy: real labelled footage
does that (4.6).
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import numpy as np
import pytest

from app.analysis.contact import CONFIDENCE_FLOOR, detect_contact_frame
from app.analysis.handedness import detect_handedness
from app.analysis.normalize import normalize_sequence
from app.analysis.segmentation import (
    ACCEPT_CONFIDENCE,
    SEGMENTATION_VERSION,
    SegmentationResult,
    SwingAnalysis,
    build_windows,
    find_candidates,
    landmark_digest,
    refractory_frames,
    rolling_torso_scale,
    score_swing,
    segment_session,
    session_speed_signal,
    slice_pose_sequence,
    status_for_confidence,
    tracking_segments,
    valley_ratio,
)
from app.models.enums import Handedness, HandednessSource, ShotType
from app.models.session import SwingRejection, SwingStatus
from tests.unit.analysis.synthetic_session import (
    FPS,
    Session,
    Swing,
    build_session,
    evenly_spaced,
)

RIGHT = Handedness.RIGHT


def run(session: Session, hint: Handedness = RIGHT) -> SegmentationResult:
    return segment_session(build_session(session), hint)


def accepted(result: SegmentationResult) -> list[SwingAnalysis]:
    return [s for s in result.swings if s.status is SwingStatus.ACCEPTED]


def peak_times(swings: list[SwingAnalysis], result: SegmentationResult) -> list[float]:
    return [round(float(result.signal.timestamps_s[s.window.peak_index]), 2) for s in swings]


# ---------------------------------------------------------------- counting


def test_ten_swings_yield_ten_windows() -> None:
    result = run(Session(duration_s=32, swings=evenly_spaced(10)))
    assert len(result.swings) == 10
    assert len(accepted(result)) == 10
    assert peak_times(accepted(result), result) == [2.0 + 3.0 * i for i in range(10)]
    assert result.rejection_counts == {}


def test_contact_sits_on_each_swing() -> None:
    result = run(Session(duration_s=32, swings=evenly_spaced(10)))
    for swing in accepted(result):
        assert swing.contact is not None
        global_contact = swing.window.start + swing.contact.frame_index
        assert abs(global_contact - swing.window.peak_index) <= 3


def test_faster_swing_does_not_hide_slower_neighbours() -> None:
    swings = [
        Swing(2.0), Swing(5.0, scale=1.8), Swing(8.0, scale=0.7),
        Swing(11.0), Swing(14.0, scale=0.7), Swing(17.0),
    ]
    result = run(Session(duration_s=20, swings=swings))
    assert peak_times(accepted(result), result) == [2.0, 5.0, 8.0, 11.0, 14.0, 17.0]


def test_refractory_merges_double_peak_within_one_swing() -> None:
    seq = build_session(Session(duration_s=6, swings=[Swing(2.0, stutter_s=0.2)]))
    signal = session_speed_signal(seq, RIGHT)
    drive = signal.speed[55:72]
    local_maxima = [
        i for i in range(1, drive.size - 1)
        if drive[i] >= drive[i - 1] and drive[i] >= drive[i + 1] and drive[i] > 3.0
    ]
    assert len(local_maxima) == 2, "fixture must actually contain a double peak"
    result = segment_session(seq, RIGHT)
    assert len(result.candidates) == 1
    assert len(result.swings) == 1


def test_two_swings_at_one_second_get_disjoint_windows() -> None:
    result = run(Session(duration_s=6, swings=[Swing(2.0), Swing(3.0)]))
    first, second = result.swings
    assert first.window.end <= second.window.start
    assert first.status is SwingStatus.ACCEPTED
    assert second.status is SwingStatus.ACCEPTED
    # Slices under 2 s are an unexercised path for Stages 7-15 (4.2 step 3):
    # the full stack must run on them without raising.
    assert second.end_time_s - second.start_time_s < 2.0
    for swing in (first, second):
        score = score_swing(swing)
        assert score is not None
        assert score.scorecard is not None


def test_close_swings_keep_their_build_up() -> None:
    """The split leaves the later swing its minimum 0.5 s before the peak.

    The plain minimum-speed frame is the takeback turn, just before the drive
    (the deviation documented in ``_split_frame``).
    """
    result = run(Session(duration_s=6, swings=[Swing(2.0), Swing(3.0)]))
    times = result.signal.timestamps_s
    second = result.swings[1]
    assert times[second.window.peak_index] - times[second.window.start] >= 0.5 - 1e-6


def test_swings_too_close_for_both_windows_reject_one_as_too_short() -> None:
    result = run(Session(duration_s=6, swings=[Swing(2.0), Swing(2.65)]))
    reasons = [s.rejection for s in result.swings]
    assert SwingRejection.WINDOW_TOO_SHORT in reasons
    assert result.rejection_counts.get("window_too_short", 0) >= 1


@pytest.mark.parametrize(
    "spacings",
    list(itertools.islice(itertools.permutations([0.9, 1.2, 1.7, 2.5, 4.0]), 0, 120, 17)),
)
def test_windows_never_overlap(spacings: tuple[float, ...]) -> None:
    contacts = list(np.cumsum((2.0,) + spacings))
    session = Session(duration_s=contacts[-1] + 2.0, swings=[Swing(c) for c in contacts])
    result = run(session)
    windows = sorted((s.window for s in result.swings), key=lambda w: w.start)
    for left, right in zip(windows, windows[1:]):
        assert left.end <= right.start
    for window in windows:
        assert window.start <= window.peak_index < window.end


# --------------------------------------------------------- non-swing motion


def test_walking_arm_motion_produces_no_candidate() -> None:
    result = run(Session(duration_s=20, walk_speed_tu_s=1.0, arm_swing_tu=0.3))
    assert result.candidates == ()
    assert result.swings == ()


def test_low_amplitude_bounce_produces_no_candidate() -> None:
    result = run(Session(duration_s=20, bounce_tu=0.15))
    assert result.candidates == ()


def test_occlusion_spike_is_not_a_candidate() -> None:
    result = run(Session(duration_s=10, occlusion_frames=[100, 200]))
    assert result.candidates == ()


def test_dead_time_gives_nothing() -> None:
    result = run(Session(duration_s=30))
    assert result.swings == ()
    assert result.rejection_counts == {}


# ------------------------------------------------------- tracking segments


def test_gap_longer_than_three_frames_splits_segments() -> None:
    result = run(Session(duration_s=10, swings=[Swing(2.0), Swing(8.0)], holes=[(140, 146)]))
    assert result.signal.segments == ((0, 140), (146, 300))
    assert peak_times(accepted(result), result) == [2.0, 8.0]


def test_short_gap_does_not_split() -> None:
    result = run(Session(duration_s=10, swings=[Swing(2.0)], holes=[(140, 143)]))
    assert len(result.signal.segments) == 1


def test_identity_jump_rejects_only_the_affected_window() -> None:
    result = run(Session(duration_s=17, swings=evenly_spaced(5), identity_jump_s=8.1))
    assert len(result.signal.segments) == 2
    assert peak_times(accepted(result), result) == [2.0, 5.0, 11.0, 14.0]
    rejected = [c for c in result.candidates if c.rejection is not None] + [
        s for s in result.swings if s.status is SwingStatus.REJECTED
    ]
    assert len(rejected) == 1


def test_player_size_doubling_does_not_change_swing_count() -> None:
    result = run(
        Session(
            duration_s=32,
            swings=evenly_spaced(10),
            torso_fraction=0.1,
            torso_fraction_end=0.2,
        )
    )
    assert len(accepted(result)) == 10
    peaks = [c.peak_speed_tu_s for c in result.candidates]
    assert max(peaks) / min(peaks) < 1.15, "rolling TU must cancel the size change"


def test_rolling_torso_scale_tracks_time_not_frames() -> None:
    times = np.arange(600, dtype=np.float64) / FPS
    lengths = np.linspace(0.1, 0.2, 600)
    valid = np.ones(600, dtype=bool)
    scale = rolling_torso_scale(lengths, valid, times)
    assert scale[300] == pytest.approx(lengths[300], rel=1e-6)
    assert scale[0] == pytest.approx(np.median(lengths[: 5 * 30 + 1]), rel=1e-6)


def test_rolling_scale_fills_frames_far_from_any_valid_frame() -> None:
    times = np.arange(900, dtype=np.float64) / FPS
    valid = np.zeros(900, dtype=bool)
    valid[:30] = True
    scale = rolling_torso_scale(np.full(900, 0.2), valid, times)
    assert np.all(scale == pytest.approx(0.2))


def test_rolling_scale_is_zero_without_valid_frames() -> None:
    scale = rolling_torso_scale(np.full(10, 0.2), np.zeros(10, dtype=bool), np.arange(10.0))
    assert np.all(scale == 0.0)


def test_tracking_segments_empty_without_valid_frames() -> None:
    xy = np.zeros((5, 33, 2))
    assert tracking_segments(xy, np.zeros(5, dtype=bool), np.ones(5)) == ()


# ------------------------------------------------------ direction and hands


def test_mixed_swing_directions_both_detected() -> None:
    swings = [Swing(2.0), Swing(5.0, direction=-1), Swing(8.0), Swing(11.0, direction=-1)]
    result = run(Session(duration_s=14, swings=swings))
    assert peak_times(accepted(result), result) == [2.0, 5.0, 8.0, 11.0]


def test_handedness_is_locked_to_hint_across_windows() -> None:
    result = run(Session(duration_s=20, swings=evenly_spaced(6)))
    for swing in accepted(result):
        assert swing.handedness is not None
        assert swing.handedness.handedness is RIGHT
        assert swing.handedness.source is HandednessSource.USER_HINT


def test_left_hander_is_segmented_on_the_left_wrist() -> None:
    session = Session(duration_s=8, swings=[Swing(2.0), Swing(5.0)], handedness=Handedness.LEFT)
    assert len(accepted(run(session, Handedness.LEFT))) == 2
    # Hinting the wrong hand watches a wrist that barely moves.
    assert accepted(run(session, RIGHT)) == []


def test_hint_disagreeing_with_detection_keeps_hint_and_warns() -> None:
    session = Session(duration_s=8, swings=[Swing(2.0)], handedness=Handedness.LEFT)
    result = run(session, RIGHT)
    for swing in result.swings:
        if swing.handedness is not None:
            assert swing.handedness.handedness is RIGHT
            assert any("hint" in w for w in swing.handedness.warnings)


def test_unknown_hint_is_refused() -> None:
    seq = build_session(Session(duration_s=4, swings=[Swing(2.0)]))
    with pytest.raises(ValueError):
        segment_session(seq, Handedness.UNKNOWN)


# --------------------------------------------------------------- confidence


def test_slice_prominence_recovers_above_floor() -> None:
    """The 0.35 floor regression (4.1): whole-session Stage 9 sees every other
    swing as a rival; a one-swing slice does not."""
    seq = build_session(Session(duration_s=32, swings=evenly_spaced(10)))
    whole, quality = normalize_sequence(seq)
    hand = detect_handedness(whole, RIGHT)
    whole_contact = detect_contact_frame(whole, hand, quality)
    assert whole_contact.confidence <= CONFIDENCE_FLOOR + 0.05

    result = segment_session(seq, RIGHT)
    assert all((s.confidence or 0.0) >= ACCEPT_CONFIDENCE for s in result.swings)


@pytest.mark.parametrize(
    ("confidence", "status"),
    [
        (0.9, SwingStatus.ACCEPTED),
        (0.45, SwingStatus.ACCEPTED),
        (0.44, SwingStatus.LOW_CONFIDENCE),
        (0.35, SwingStatus.LOW_CONFIDENCE),
        (0.34, SwingStatus.REJECTED),
    ],
)
def test_confidence_bands(confidence: float, status: SwingStatus) -> None:
    assert status_for_confidence(confidence) is status


def test_rejected_swings_are_never_scored() -> None:
    result = run(Session(duration_s=6, swings=[Swing(2.0), Swing(2.65)]))
    for swing in result.swings:
        if swing.status is SwingStatus.REJECTED:
            assert score_swing(swing) is None


def test_accepted_swing_scores_as_a_forehand() -> None:
    result = run(Session(duration_s=5, swings=[Swing(2.0)]))
    score = score_swing(result.swings[0])
    assert score is not None
    assert score.shot_type.shot_type is ShotType.FOREHAND_TOPSPIN
    assert score.scorecard.overall_score is not None


# ------------------------------------------------------------------ filters


def test_valley_ratio_uses_the_higher_valley() -> None:
    speed = np.array([0.0, 2.0, 1.0, 10.0, 4.0, 6.0, 0.0])
    assert valley_ratio(speed, 3, 1, 5) == pytest.approx(10.0 / 4.0)
    assert valley_ratio(speed, 3, 0, 6) is None


def test_not_prominent_peak_is_rejected() -> None:
    from app.analysis.segmentation import SessionSignal

    speed = np.zeros(200)
    speed[40:61] = np.concatenate([np.linspace(4, 10, 11), np.linspace(10, 4, 10)])
    speed[61:80] = 4.0
    speed[80:101] = np.concatenate([np.linspace(4, 6, 11), np.linspace(6, 4, 10)])
    signal = SessionSignal(
        speed=speed,
        reliable=np.ones(200, dtype=bool),
        timestamps_s=np.arange(200) / FPS,
        rolling_scale=np.ones(200),
        segments=((0, 200),),
        segment_of=np.zeros(200, dtype=np.int64),
    )
    candidates, _ = find_candidates(signal, separation_frames=18)
    by_index = {c.peak_index: c.rejection for c in candidates}
    assert by_index[50] is None
    assert by_index[90] is SwingRejection.NOT_PROMINENT


def test_relative_floor_rejects_small_peaks_among_big_ones() -> None:
    from app.analysis.segmentation import SessionSignal

    speed = np.zeros(600)
    for centre, height in ((50, 20.0), (150, 20.0), (250, 20.0), (350, 4.0), (450, 20.0)):
        speed[centre - 5 : centre + 6] = height * (1 - np.abs(np.arange(-5, 6)) / 6)
    signal = SessionSignal(
        speed=speed,
        reliable=np.ones(600, dtype=bool),
        timestamps_s=np.arange(600) / FPS,
        rolling_scale=np.ones(600),
        segments=((0, 600),),
        segment_of=np.zeros(600, dtype=np.int64),
    )
    candidates, _ = find_candidates(signal, separation_frames=18)
    rejected = {c.peak_index: c.rejection for c in candidates if c.rejection is not None}
    assert rejected == {350: SwingRejection.BELOW_RELATIVE_FLOOR}
    windows = build_windows(signal, candidates)
    assert [w.peak_index for w in windows] == [50, 150, 250, 450]


def test_refractory_is_point_six_seconds_in_frames() -> None:
    assert refractory_frames(np.arange(100) / 30.0) == 18
    assert refractory_frames(np.arange(100) / 60.0) == 36
    assert refractory_frames(np.array([0.0])) == 1


# ----------------------------------------------------------------- timing


def test_contact_absolute_time_uses_source_pts() -> None:
    seq = build_session(Session(duration_s=5, swings=[Swing(2.0)], start_s=100.0))
    swing = segment_session(seq, RIGHT).swings[0]
    assert swing.contact is not None
    global_index = swing.window.start + swing.contact.frame_index
    assert swing.contact.contact_absolute_time_s == pytest.approx(
        float(seq.timestamps_s[global_index])
    )
    shifted = segment_session(seq, RIGHT, source_start_s=7.5).swings[0]
    assert shifted.contact is not None
    assert shifted.contact.contact_absolute_time_s == pytest.approx(
        swing.contact.contact_absolute_time_s + 7.5
    )


def test_slice_keeps_timestamps() -> None:
    seq = build_session(Session(duration_s=5, start_s=10.0))
    piece = slice_pose_sequence(seq, 30, 60)
    assert piece.timestamps_s[0] == pytest.approx(11.0)
    assert piece.landmarks.shape[0] == 30
    with pytest.raises(ValueError):
        slice_pose_sequence(seq, 60, 60)


# ------------------------------------------------------------ determinism


def test_same_input_gives_identical_output() -> None:
    session = Session(duration_s=20, swings=evenly_spaced(6), walk_speed_tu_s=0.2)
    first, second = run(session), run(session)
    assert first.landmark_digest == second.landmark_digest
    assert first.rejection_counts == second.rejection_counts
    assert [s.window for s in first.swings] == [s.window for s in second.swings]
    assert [s.confidence for s in first.swings] == [s.confidence for s in second.swings]
    assert first.version == SEGMENTATION_VERSION


def test_digest_changes_with_the_landmarks() -> None:
    a = build_session(Session(duration_s=4, swings=[Swing(2.0)]))
    b = build_session(Session(duration_s=4, swings=[Swing(2.1)]))
    assert landmark_digest(a) != landmark_digest(b)


def test_empty_sequence_is_handled() -> None:
    seq = build_session(Session(duration_s=0.0))
    result = segment_session(seq, RIGHT)
    assert result.swings == ()
    assert result.candidates == ()


def test_module_has_no_io_imports() -> None:
    path = Path(__file__).resolve().parents[3] / "app" / "analysis" / "segmentation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not names & {"os", "pathlib", "time", "socket", "cv2", "mediapipe", "av"}
    assert not any(n.startswith("app.services") for n in names)
