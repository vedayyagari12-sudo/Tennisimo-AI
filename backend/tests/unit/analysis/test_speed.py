"""Stage 11 unit tests (PIPELINE.md Stage 11, 4.5). Synthetic data only.

Every fixture has a true speed known BY CONSTRUCTION: a straight-line track at
a fixed px-per-frame rate, a fixed dt and a fixed px_per_m, so the expected mph
is computable by hand and is written out in the test.

The reference fixture: 90 px per frame at 30 fps = 2700 px/s; at 90 px/m that
is 30.0 m/s exactly; 30.0 * 2.23694 = 67.108 mph -> 67.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.analysis.speed import (
    MIN_PLAUSIBLE_MPH,
    WINDOW_S,
    blob_size_drift_ratio,
    compute_ball_speed,
    confidence_from_detections,
)
from app.ball.track import BallTrack, TERMINATED_WINDOW_END
from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    CameraView,
)
from app.models.responses import BallSpeedResult

PX_PER_M = 90.0
STEP_PX = 90.0
FPS = 30.0
EXPECTED_MPH = 67  # 90 px/frame * 30 fps / 90 px/m = 30 m/s = 67.108 mph


def make_track(
    detections_used: int,
    *,
    fps: float = FPS,
    step_px: float = STEP_PX,
    minor_first: float = 6.0,
    minor_ratio: float = 1.0,
) -> tuple[BallTrack, np.ndarray]:
    """A straight-line track whose speed is ``step_px * fps`` px/s.

    ``detections_used`` is the count AFTER Stage 11 discards the first in-window
    detection, so the track is built with one extra row up front. The minor axis
    is flat except for the last row, so the drift ratio measured over the
    post-discard window is exactly ``minor_ratio``.
    """
    count = int(detections_used) + 1
    xy = np.array([[i * step_px, 0.0] for i in range(count)], dtype=np.float32)
    minor = np.full((count,), minor_first, dtype=np.float32)
    if count >= 2:
        minor[-1] = np.float32(minor_first * minor_ratio)
    track = BallTrack(
        xy_px=xy,
        frame_offsets=np.arange(count, dtype=np.int32),
        minor_axis_px=minor,
        major_axis_px=np.full((count,), minor_first * 1.2, dtype=np.float32),
        tier=np.ones((count,), dtype=np.uint8),
        terminated_by=TERMINATED_WINDOW_END,
        coasted_frames=0,
    )
    timestamps = np.arange(count, dtype=np.float64) / float(fps)
    return track, timestamps


def run(
    track: BallTrack | None,
    timestamps: np.ndarray,
    **overrides: object,
) -> BallSpeedResult:
    """compute_ball_speed with healthy defaults; overrides isolate one gate."""
    kwargs: dict[str, object] = {
        "timestamps_s": timestamps,
        "contact_abs_s": 0.0,
        "contact_confidence": 0.9,
        "px_per_m": PX_PER_M,
        "segment_px": 1000.0,
        "is_custom_reference": False,
        "estimated_camera_view": CameraView.SIDE_ON,
    }
    kwargs.update(overrides)
    result = compute_ball_speed(track, **kwargs)  # type: ignore[arg-type]
    # Case 7: the pairing invariant holds for EVERY case this suite produces.
    assert (result.ball_speed_mph is None) == (result.unavailable_reason is not None)
    return result


# --------------------------------------------------------------------------- #
# 1. Clean track, known constant velocity
# --------------------------------------------------------------------------- #


def test_clean_constant_velocity_track_matches_hand_calculation() -> None:
    track, timestamps = make_track(6)
    result = run(track, timestamps)
    assert result.ball_speed_mph == EXPECTED_MPH
    assert result.detections_used == 6
    assert result.confidence == BallSpeedConfidence.MEDIUM
    assert result.unavailable_reason is None


def test_variable_dt_is_normalized_per_step() -> None:
    """VFR: halving one dt while halving its displacement leaves the median."""
    track, timestamps = make_track(6)
    shifted = timestamps.copy()
    shifted[3:] += 1.0 / (2.0 * FPS)  # one longer step, then a constant offset
    xy = np.asarray(track.xy_px, dtype=np.float64).copy()
    xy[3:, 0] += STEP_PX / 2.0  # matching extra displacement -> same px/s
    result = run(dataclasses.replace(track, xy_px=xy.astype(np.float32)), shifted)
    assert result.ball_speed_mph == EXPECTED_MPH


# --------------------------------------------------------------------------- #
# 2. Median outlier tolerance
# --------------------------------------------------------------------------- #


def test_one_grossly_wrong_step_does_not_change_the_answer() -> None:
    clean_track, timestamps = make_track(6)
    clean = run(clean_track, timestamps)

    corrupted = np.asarray(clean_track.xy_px, dtype=np.float64).copy()
    corrupted[4, 0] += 900.0  # a decoy blob accepted once
    corrupted[5:, 0] += 900.0  # the rest of the track carries on from there
    result = run(
        dataclasses.replace(clean_track, xy_px=corrupted.astype(np.float32)), timestamps
    )
    assert result.ball_speed_mph == clean.ball_speed_mph == EXPECTED_MPH


# --------------------------------------------------------------------------- #
# 3. First-step discard
# --------------------------------------------------------------------------- #


def test_first_step_is_discarded_before_the_median() -> None:
    track, timestamps = make_track(6)
    merged = np.asarray(track.xy_px, dtype=np.float64).copy()
    merged[0, 0] -= 700.0  # racket-merged centroid: a huge, wrong first step
    result = run(
        dataclasses.replace(track, xy_px=merged.astype(np.float32)), timestamps
    )
    assert result.ball_speed_mph == EXPECTED_MPH
    assert result.detections_used == 6


# --------------------------------------------------------------------------- #
# 4. Confidence tier boundaries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (3, BallSpeedConfidence.LOW),
        (4, BallSpeedConfidence.LOW),
        (5, BallSpeedConfidence.MEDIUM),
        (8, BallSpeedConfidence.MEDIUM),
        (9, BallSpeedConfidence.HIGH),
    ],
)
def test_confidence_tier_boundaries(n: int, expected: BallSpeedConfidence) -> None:
    # 9+ detections need >36 fps by construction (§11.5), hence 60 fps here.
    fps = 60.0 if n + 1 > int(WINDOW_S * FPS) + 1 else FPS
    track, timestamps = make_track(n, fps=fps, step_px=STEP_PX * FPS / fps)
    result = run(track, timestamps)
    assert result.detections_used == n
    assert result.confidence == expected
    assert result.ball_speed_mph == EXPECTED_MPH


def test_two_detections_is_unavailable_too_few_detections() -> None:
    track, timestamps = make_track(2)
    result = run(track, timestamps)
    assert result.ball_speed_mph is None
    assert result.confidence == BallSpeedConfidence.UNAVAILABLE
    assert result.unavailable_reason == BallSpeedUnavailableReason.TOO_FEW_DETECTIONS
    assert result.detections_used == 2


def test_confidence_from_detections_boundaries_directly() -> None:
    assert confidence_from_detections(0) == BallSpeedConfidence.UNAVAILABLE
    assert confidence_from_detections(2) == BallSpeedConfidence.UNAVAILABLE
    assert confidence_from_detections(3) == BallSpeedConfidence.LOW
    assert confidence_from_detections(4) == BallSpeedConfidence.LOW
    assert confidence_from_detections(5) == BallSpeedConfidence.MEDIUM
    assert confidence_from_detections(8) == BallSpeedConfidence.MEDIUM
    assert confidence_from_detections(9) == BallSpeedConfidence.HIGH


def test_window_excludes_detections_past_025_s() -> None:
    """Detections beyond contact + 0.25 s never reach the estimate."""
    track, timestamps = make_track(6)
    late = timestamps.copy()
    late[5:] += 1.0  # rows 5 and 6 fall outside the window
    result = run(track, late)
    assert result.detections_used == 4
    assert result.confidence == BallSpeedConfidence.LOW


# --------------------------------------------------------------------------- #
# 5. Each downward cap, individually
# --------------------------------------------------------------------------- #


def _high_track() -> tuple[BallTrack, np.ndarray]:
    """An otherwise-HIGH track (9 detections at 60 fps, same 67 mph)."""
    return make_track(9, fps=60.0, step_px=STEP_PX / 2.0)


def test_high_baseline_is_high_before_any_cap() -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps)
    assert result.confidence == BallSpeedConfidence.HIGH
    assert result.ball_speed_mph == EXPECTED_MPH


@pytest.mark.parametrize("view", [CameraView.FRONT, CameraView.BEHIND])
def test_front_and_behind_views_are_refused(view: CameraView) -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, estimated_camera_view=view)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.CAMERA_VIEW_UNSUITABLE


def test_oblique_view_caps_low() -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, estimated_camera_view=CameraView.OBLIQUE)
    assert result.ball_speed_mph == EXPECTED_MPH
    assert result.confidence == BallSpeedConfidence.LOW


@pytest.mark.parametrize("ratio", [1.0, 0.80, 1.30])
def test_blob_drift_inside_the_no_penalty_band_does_not_cap(ratio: float) -> None:
    track, timestamps = make_track(9, fps=60.0, step_px=STEP_PX / 2.0, minor_ratio=ratio)
    result = run(track, timestamps)
    assert result.confidence == BallSpeedConfidence.HIGH


@pytest.mark.parametrize("ratio", [0.70, 1.50])
def test_blob_drift_in_the_warning_band_caps_low(ratio: float) -> None:
    track, timestamps = make_track(9, fps=60.0, step_px=STEP_PX / 2.0, minor_ratio=ratio)
    result = run(track, timestamps)
    assert result.ball_speed_mph == EXPECTED_MPH
    assert result.confidence == BallSpeedConfidence.LOW


@pytest.mark.parametrize("ratio", [0.50, 2.00])
def test_blob_drift_outside_the_gate_is_refused(ratio: float) -> None:
    track, timestamps = make_track(9, fps=60.0, step_px=STEP_PX / 2.0, minor_ratio=ratio)
    result = run(track, timestamps)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.DEPTH_DRIFT_EXCEEDED


def test_blob_drift_ratio_is_not_computable_from_a_degenerate_axis_array() -> None:
    assert blob_size_drift_ratio(np.zeros((1,), dtype=np.float64)) is None
    assert blob_size_drift_ratio(np.array([0.0, 6.0])) is None
    assert blob_size_drift_ratio(np.array([6.0, 3.0])) == pytest.approx(0.5)


def test_custom_reference_caps_medium() -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, is_custom_reference=True)
    assert result.ball_speed_mph == EXPECTED_MPH
    assert result.confidence == BallSpeedConfidence.MEDIUM


@pytest.mark.parametrize(
    ("segment_px", "expected"),
    [(1000.0, BallSpeedConfidence.HIGH), (100.0, BallSpeedConfidence.LOW)],
)
def test_segment_length_bands(segment_px: float, expected: BallSpeedConfidence) -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, segment_px=segment_px)
    assert result.confidence == expected
    assert result.ball_speed_mph == EXPECTED_MPH


def test_segment_below_60_px_is_refused() -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, segment_px=59.0)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.CALIBRATION_IMPLAUSIBLE


def test_low_contact_confidence_is_refused() -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, contact_confidence=0.34)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.CONTACT_UNRELIABLE


def test_median_displacement_below_the_noise_floor_is_refused() -> None:
    # 3 px per frame: below the 4 px floor, so no number is reported.
    track, timestamps = make_track(6, step_px=3.0)
    result = run(track, timestamps)
    assert result.ball_speed_mph is None
    assert (
        result.unavailable_reason
        == BallSpeedUnavailableReason.DISPLACEMENT_BELOW_NOISE_FLOOR
    )


def test_speed_below_the_plausibility_floor_is_refused() -> None:
    # 5 px/frame at 30 fps = 150 px/s / 90 px/m = 1.67 m/s = 3.7 mph.
    track, timestamps = make_track(6, step_px=5.0)
    result = run(track, timestamps)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.IMPLAUSIBLE_SPEED


def test_speed_above_the_plausibility_ceiling_is_refused() -> None:
    # 250 px/frame at 30 fps = 7500 px/s / 90 px/m = 83.3 m/s = 186 mph.
    track, timestamps = make_track(6, step_px=250.0)
    result = run(track, timestamps)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.IMPLAUSIBLE_SPEED


def test_unusable_calibration_scale_is_not_calibrated() -> None:
    track, timestamps = _high_track()
    result = run(track, timestamps, px_per_m=0.0)
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.NOT_CALIBRATED


# --------------------------------------------------------------------------- #
# 6. track is None
# --------------------------------------------------------------------------- #


def test_missing_track_without_a_reason_reports_no_track_seeded() -> None:
    result = run(None, np.arange(8, dtype=np.float64) / FPS)
    assert result.ball_speed_mph is None
    assert result.confidence == BallSpeedConfidence.UNAVAILABLE
    assert result.unavailable_reason == BallSpeedUnavailableReason.NO_TRACK_SEEDED
    assert result.detections_used == 0


def test_missing_track_passes_the_detector_reason_through() -> None:
    result = run(
        None,
        np.arange(8, dtype=np.float64) / FPS,
        detection_reason=BallSpeedUnavailableReason.DETECTION_TIMEOUT,
    )
    assert result.unavailable_reason == BallSpeedUnavailableReason.DETECTION_TIMEOUT


# --------------------------------------------------------------------------- #
# 7. Pairing invariant (also asserted inside `run` for every case above)
# --------------------------------------------------------------------------- #


def test_pairing_invariant_is_enforced_by_the_model_itself() -> None:
    with pytest.raises(ValueError):
        BallSpeedResult(
            ball_speed_mph=None,
            confidence=BallSpeedConfidence.UNAVAILABLE,
            detections_used=0,
            unavailable_reason=None,
        )
    with pytest.raises(ValueError):
        BallSpeedResult(
            ball_speed_mph=MIN_PLAUSIBLE_MPH,
            confidence=BallSpeedConfidence.LOW,
            detections_used=3,
            unavailable_reason=BallSpeedUnavailableReason.IMPLAUSIBLE_SPEED,
        )


# --------------------------------------------------------------------------- #
# 8. Never raises
# --------------------------------------------------------------------------- #


def test_empty_track_returns_a_safe_unavailable_result() -> None:
    empty = BallTrack(
        xy_px=np.zeros((0, 2), dtype=np.float32),
        frame_offsets=np.zeros((0,), dtype=np.int32),
        minor_axis_px=np.zeros((0,), dtype=np.float32),
        major_axis_px=np.zeros((0,), dtype=np.float32),
        tier=np.zeros((0,), dtype=np.uint8),
        terminated_by=TERMINATED_WINDOW_END,
        coasted_frames=0,
    )
    result = run(empty, np.zeros((0,), dtype=np.float64))
    assert result.ball_speed_mph is None
    assert result.unavailable_reason == BallSpeedUnavailableReason.TOO_FEW_DETECTIONS


def test_mismatched_shapes_and_offsets_return_a_safe_result() -> None:
    track, _ = make_track(6)
    for timestamps in (
        np.zeros((0,), dtype=np.float64),  # no timestamps at all
        np.array([0.0, 0.01], dtype=np.float64),  # far too few for the offsets
        np.full((7,), np.nan, dtype=np.float64),  # non-finite PTS
    ):
        result = run(track, timestamps)
        assert result.ball_speed_mph is None
        assert result.unavailable_reason is not None


def test_garbage_scalar_inputs_do_not_raise() -> None:
    track, timestamps = make_track(6)
    for overrides in (
        {"px_per_m": float("nan")},
        {"segment_px": float("nan")},
        {"contact_confidence": float("nan")},
        {"contact_abs_s": float("inf")},
    ):
        result = run(track, timestamps, **overrides)  # type: ignore[arg-type]
        assert result.ball_speed_mph is None
