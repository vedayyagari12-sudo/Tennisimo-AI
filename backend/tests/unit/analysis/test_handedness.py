"""Stage 8 unit tests (PIPELINE.md Stage 8, 4.5). Synthetic data only."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.analysis.handedness import (
    HANDEDNESS_MISMATCH_NOTE,
    HINT_CONFIDENCE_FLOOR,
    detect_handedness,
    normalized_margin,
    observed_path_length,
    peak_radial_distance,
    racket_wrist_index,
    visible_fraction,
)
from app.analysis.normalize import LEFT_WRIST, RIGHT_WRIST
from app.models.enums import Handedness, HandednessSource
from tests.unit.analysis.synthetic import make_normalized_sequence, triangular_speed

FRAME_COUNT = 61
PEAK_FRAME = 30


def swing(handedness: Handedness = Handedness.RIGHT):
    speed = triangular_speed(FRAME_COUNT, PEAK_FRAME, 12.0)
    return make_normalized_sequence(speed, handedness=handedness)


def two_handed_swing(similarity: float = 0.99):
    """Both wrists move almost identically -- the known failure case."""
    seq = swing()
    points = seq.points.copy()
    points[:, LEFT_WRIST, :] = points[:, RIGHT_WRIST, :] * similarity
    return dataclasses.replace(seq, points=points)


def test_normalized_margin_is_the_clipped_share_difference() -> None:
    assert normalized_margin(0.7, 0.3) == pytest.approx(0.8)
    assert normalized_margin(0.5, 0.5) == 0.0
    assert normalized_margin(10.0, 0.0) == 1.0  # clipped, not 2.0
    assert normalized_margin(0.0, 0.0) == 0.0


def test_peak_radial_distance_is_the_largest_distance_from_mid_hip() -> None:
    points = np.array([[0.0, 0.0], [3.0, 4.0], [1.0, 1.0]])
    assert peak_radial_distance(points) == 5.0
    assert peak_radial_distance(np.zeros((0, 2))) == 0.0


def test_right_handed_swing_detects_right_with_high_confidence() -> None:
    result = detect_handedness(swing(Handedness.RIGHT))
    assert result.handedness == Handedness.RIGHT
    assert result.source == HandednessSource.DETECTED
    assert result.confidence > 0.8
    assert result.racket_hand_path_length_tu is not None
    assert result.off_hand_path_length_tu is not None
    assert result.racket_hand_path_length_tu > result.off_hand_path_length_tu


def test_mirrored_swing_detects_left() -> None:
    result = detect_handedness(swing(Handedness.LEFT))
    assert result.handedness == Handedness.LEFT
    assert result.confidence > 0.8
    assert racket_wrist_index(result.handedness) == LEFT_WRIST


def test_two_handed_swing_has_low_confidence_and_honours_the_hint() -> None:
    seq = two_handed_swing()
    unhinted = detect_handedness(seq)
    assert unhinted.confidence < HINT_CONFIDENCE_FLOOR
    assert unhinted.source == HandednessSource.DETECTED

    hinted = detect_handedness(seq, hint=Handedness.LEFT)
    assert hinted.handedness == Handedness.LEFT
    assert hinted.source == HandednessSource.USER_HINT
    assert hinted.warnings
    assert hinted.confidence < HINT_CONFIDENCE_FLOOR


def test_a_hint_beats_even_a_confident_detection_and_keeps_it_as_a_cross_check() -> None:
    result = detect_handedness(swing(Handedness.RIGHT), hint=Handedness.LEFT)
    assert result.confidence >= HINT_CONFIDENCE_FLOOR
    assert result.handedness == Handedness.LEFT
    assert result.source == HandednessSource.USER_HINT
    assert result.detected_handedness == Handedness.RIGHT
    # Exactly ONE plain-language note, no numbers in it.
    assert result.warnings == [HANDEDNESS_MISMATCH_NOTE]
    assert not any(character.isdigit() for character in HANDEDNESS_MISMATCH_NOTE)
    # The racket-hand path is the HINTED (left) wrist's.
    assert racket_wrist_index(result.handedness) == LEFT_WRIST
    assert result.racket_hand_path_length_tu is not None
    assert result.off_hand_path_length_tu is not None
    assert result.racket_hand_path_length_tu < result.off_hand_path_length_tu


def test_a_hint_that_agrees_with_a_confident_detection_adds_nothing() -> None:
    result = detect_handedness(swing(Handedness.RIGHT), hint=Handedness.RIGHT)
    assert result.handedness == Handedness.RIGHT
    assert result.source == HandednessSource.USER_HINT
    assert result.detected_handedness == Handedness.RIGHT
    assert result.warnings == []


def test_a_low_confidence_disagreement_does_not_add_the_mismatch_note() -> None:
    """Below the floor the detection is not confident enough to contradict."""
    result = detect_handedness(two_handed_swing(), hint=Handedness.LEFT)
    assert result.confidence < HINT_CONFIDENCE_FLOOR
    assert HANDEDNESS_MISMATCH_NOTE not in result.warnings


@pytest.mark.parametrize("hint", [None, Handedness.UNKNOWN])
def test_no_concrete_hint_leaves_detection_in_charge(hint: Handedness | None) -> None:
    result = detect_handedness(swing(Handedness.LEFT), hint=hint)
    assert result.handedness == Handedness.LEFT
    assert result.source == HandednessSource.DETECTED
    assert result.detected_handedness is None
    assert result.warnings == []


def test_unknown_hint_is_ignored() -> None:
    result = detect_handedness(two_handed_swing(), hint=Handedness.UNKNOWN)
    assert result.source == HandednessSource.DETECTED


def test_disagreeing_criteria_are_tie_broken_at_the_peak_speed_frame() -> None:
    # Right wrist wins path length (it wiggles a lot close to the hips), left
    # wrist wins peak radius and is further out at the peak-speed frame.
    seq = swing()
    points = seq.points.copy()
    frames = np.arange(FRAME_COUNT, dtype=np.float64)
    points[:, RIGHT_WRIST, 0] = 0.05 * np.sin(frames * 2.0)
    points[:, RIGHT_WRIST, 1] = 0.05 * np.cos(frames * 2.0)
    points[:, LEFT_WRIST, 0] = -0.02 * frames
    points[:, LEFT_WRIST, 1] = 0.5
    seq = dataclasses.replace(seq, points=points)

    result = detect_handedness(seq)
    assert result.handedness == Handedness.LEFT
    assert result.confidence < HINT_CONFIDENCE_FLOOR
    assert any("disagree" in warning for warning in result.warnings)


def test_too_short_sequence_returns_a_result_instead_of_raising() -> None:
    seq = make_normalized_sequence(np.array([1.0]))
    result = detect_handedness(seq)
    assert result.handedness == Handedness.UNKNOWN
    assert result.confidence == 0.0
    assert result.warnings

    hinted = detect_handedness(seq, hint=Handedness.RIGHT)
    assert hinted.handedness == Handedness.RIGHT
    assert hinted.source == HandednessSource.USER_HINT


def occluded_off_hand_swing():
    """Right-handed swing whose LEFT wrist is occluded for a stretch.

    While occluded MediaPipe parks the left wrist somewhere it never was, so the
    raw accumulated path of the off hand exceeds the racket hand's -- the exact
    shape that inverted the detection on real footage.
    """
    seq = swing(Handedness.RIGHT)
    points = seq.points.copy()
    visibility = seq.visibility.copy()
    occluded = np.arange(10, 20)
    for offset, frame in enumerate(occluded):
        points[frame, LEFT_WRIST, :] = (2.0 * (-1.0) ** offset, 2.0)
    visibility[occluded, LEFT_WRIST] = 0.05
    return dataclasses.replace(seq, points=points, visibility=visibility)


def test_peak_radial_distance_ignores_occluded_frames() -> None:
    points = np.array([[0.0, 0.0], [3.0, 4.0], [1.0, 1.0]])
    visibility = np.array([0.9, 0.05, 0.9])
    assert peak_radial_distance(points, visibility) == pytest.approx(np.sqrt(2.0))
    assert peak_radial_distance(points, np.zeros(3)) == 0.0


def test_visible_fraction_is_the_share_of_visible_frames() -> None:
    assert visible_fraction(np.array([0.9, 0.9, 0.1, 0.1])) == pytest.approx(0.5)
    assert visible_fraction(np.zeros((0,))) == 0.0


def test_occluded_off_hand_does_not_invert_the_detection() -> None:
    seq = occluded_off_hand_swing()
    # The bug, pinned: without the visibility channel the off hand "travels"
    # further than the racket hand.
    raw_left = float(
        np.sum(np.linalg.norm(np.diff(seq.points[:, LEFT_WRIST, :], axis=0), axis=1))
    )
    raw_right = float(
        np.sum(np.linalg.norm(np.diff(seq.points[:, RIGHT_WRIST, :], axis=0), axis=1))
    )
    assert raw_left > raw_right

    result = detect_handedness(seq)
    assert result.handedness == Handedness.RIGHT
    assert result.racket_hand_path_length_tu is not None
    assert result.off_hand_path_length_tu is not None
    assert result.racket_hand_path_length_tu > result.off_hand_path_length_tu


def test_confidence_is_scaled_by_the_worse_observed_wrist() -> None:
    clean = detect_handedness(swing(Handedness.RIGHT))
    seq = swing(Handedness.RIGHT)
    visibility = seq.visibility.copy()
    visibility[: FRAME_COUNT // 2, LEFT_WRIST] = 0.05
    partial = detect_handedness(dataclasses.replace(seq, visibility=visibility))

    assert partial.confidence < clean.confidence
    assert any("visibility incomplete" in warning for warning in partial.warnings)


def test_an_unobservable_racket_wrist_cannot_win_confidently() -> None:
    """A wrist MediaPipe never saw must not out-argue the hint."""
    seq = swing(Handedness.RIGHT)
    visibility = seq.visibility.copy()
    visibility[:, RIGHT_WRIST] = 0.05
    seq = dataclasses.replace(seq, visibility=visibility)

    result = detect_handedness(seq, hint=Handedness.RIGHT)
    assert result.confidence < HINT_CONFIDENCE_FLOOR
    assert result.handedness == Handedness.RIGHT
    assert result.source == HandednessSource.USER_HINT


def test_observed_path_length_is_none_when_no_step_was_observed() -> None:
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]])
    assert observed_path_length(square, np.full(5, 0.9)) == pytest.approx(4.0)
    # Visible frames that are never consecutive: path_length would say 0.0.
    assert observed_path_length(square, np.array([0.9, 0.1, 0.9, 0.1, 0.9])) is None
    assert observed_path_length(square, np.zeros(5)) is None
    # No visibility channel: every step counts, as before.
    assert observed_path_length(square) == pytest.approx(4.0)
    assert observed_path_length(np.zeros((1, 2))) is None


def test_a_held_still_but_observed_wrist_is_a_measured_zero() -> None:
    still = np.zeros((5, 2))
    assert observed_path_length(still, np.full(5, 0.9)) == 0.0


def test_an_unseen_racket_wrist_reports_an_unmeasured_path_not_zero() -> None:
    seq = swing(Handedness.RIGHT)
    visibility = seq.visibility.copy()
    visibility[:, RIGHT_WRIST] = 0.05
    visibility[FRAME_COUNT // 2, RIGHT_WRIST] = 0.9  # one lone visible frame
    result = detect_handedness(dataclasses.replace(seq, visibility=visibility), hint=Handedness.RIGHT)
    assert result.handedness == Handedness.RIGHT
    assert result.racket_hand_path_length_tu is None
    assert result.off_hand_path_length_tu is not None


def test_a_stored_result_without_detected_handedness_still_validates() -> None:
    from app.models.responses import HandednessResult

    old = {"handedness": "left", "confidence": 0.73, "source": "detected", "warnings": []}
    result = HandednessResult.model_validate(old)
    assert result.detected_handedness is None
    assert HandednessResult.model_validate(result.model_dump(mode="json")) == result
