"""Build step 8: ``analysis/camera_stability.py`` (design 2.4, brief Constants).

Pure synthetic shift series. The pixel end-to-end cases (pan, shake, bump,
moving players) are in ``tests/unit/ball/test_camera_motion.py``.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from app.analysis.camera_stability import (
    CameraStability,
    StabilityVerdict,
    assess_camera_stability,
)

SEED = 20261102
LONG_EDGE = 1920
INTERVAL = 0.5


def times(duration_s: float) -> npt.NDArray[np.float64]:
    return np.arange(0.0, duration_s + 1e-9, INTERVAL)


def counts(n: int, value: int = 200) -> npt.NDArray[np.int32]:
    return np.full(n, value, dtype=np.int32)


def assess(t: npt.NDArray[np.float64], shifts: npt.NDArray[np.float64], long_edge: int = LONG_EDGE) -> CameraStability:
    return assess_camera_stability(t, shifts, counts(len(t)), long_edge)


def test_still_camera_is_stable() -> None:
    t = times(20.0)
    rng = np.random.default_rng(SEED)
    shifts = rng.normal(0.0, 0.3, (len(t), 2))
    shifts[0] = 0.0
    result = assess(t, shifts)
    assert result.verdict is StabilityVerdict.STABLE
    assert result.bumped_at_s is None
    assert result.samples == len(t)
    assert result.p95_shift_fraction is not None and result.p95_shift_fraction < 0.001
    assert result.rms_shift_px is not None and result.rms_shift_px < 1.0


def test_shake_is_shaky_and_rms_matches() -> None:
    t = times(60.0)
    rng = np.random.default_rng(SEED)
    shifts = rng.normal(0.0, 1.5, (len(t), 2))
    shifts[0] = 0.0
    result = assess(t, shifts)
    assert result.verdict is StabilityVerdict.SHAKY
    assert result.bumped_at_s is None
    expected_rms = float(np.sqrt(np.mean(np.sum(shifts**2, axis=1))))
    assert result.rms_shift_px == pytest.approx(expected_rms)


def test_pan_is_moving_not_a_bump() -> None:
    """The settle rule: a pan never comes to rest, so it is not a bump."""
    t = times(20.0)
    shifts = np.column_stack([20.0 * t, np.zeros_like(t)])
    result = assess(t, shifts)
    assert result.verdict is StabilityVerdict.MOVING
    assert result.bumped_at_s is None


def test_bump_truncates_and_pre_bump_samples_judge_the_verdict() -> None:
    t = times(15.0)
    shifts = np.zeros((len(t), 2))
    shifts[t >= 10.0] = (8.0, 0.0)  # 0.42 % of 1920, then still
    result = assess(t, shifts)
    assert result.bumped_at_s == 10.0
    assert result.verdict is StabilityVerdict.STABLE
    assert result.p95_shift_fraction == 0.0
    assert result.rms_shift_px == 0.0


def test_bump_with_wobble_starts_at_the_knock() -> None:
    t = times(15.0)
    shifts = np.zeros((len(t), 2))
    shifts[t >= 10.0] = (8.0, 0.0)
    shifts[t == 10.0] = (14.0, 3.0)  # wobble while settling
    shifts[t == 10.5] = (5.0, -2.0)
    assert assess(t, shifts).bumped_at_s == 10.0


def test_wander_that_rests_late_is_not_a_bump() -> None:
    """A hand wandering for 6 s (about the real clip's rate, scaled to 1920)
    that then holds still is moving, not knocked."""
    t = times(12.0)
    shifts = np.column_stack([np.minimum(t, 6.0) * 6.0, np.sin(2.0 * t) * 8.0])
    shifts[t >= 6.0] = (36.0, 0.0)
    result = assess(t, shifts)
    assert result.bumped_at_s is None
    assert result.verdict is StabilityVerdict.MOVING


def test_settle_may_follow_a_second_of_wobble_but_not_more() -> None:
    t = times(15.0)
    prompt = np.zeros((len(t), 2))
    prompt[t >= 5.0] = (8.0, 0.0)
    prompt[(t >= 5.0) & (t <= 5.5)] = (16.0, 6.0)  # wobble, rest from 6.0
    slow = prompt.copy()
    slow[(t >= 5.0) & (t <= 6.5)] = np.array([(16.0, 6.0), (4.0, -6.0), (14.0, 5.0), (5.0, -5.0)])
    assert assess(t, prompt).bumped_at_s == 5.0
    assert assess(t, slow).bumped_at_s is None


def test_bump_needs_one_second() -> None:
    """Three samples at 2 Hz span 1 s; two span 0.5 s and are not a bump."""
    t = times(10.0)
    long_one = np.zeros((len(t), 2))
    long_one[(t >= 5.0) & (t <= 6.0)] = (8.0, 0.0)
    short_one = np.zeros((len(t), 2))
    short_one[(t >= 5.0) & (t <= 5.5)] = (8.0, 0.0)
    assert assess(t, long_one).bumped_at_s == 5.0
    assert assess(t, short_one).bumped_at_s is None


def test_bump_threshold_is_two_tenths_of_a_percent() -> None:
    t = times(10.0)
    at = np.zeros((len(t), 2))
    at[t >= 5.0] = (0.002 * LONG_EDGE, 0.0)
    under = np.zeros((len(t), 2))
    under[t >= 5.0] = (0.002 * LONG_EDGE - 0.01, 0.0)
    assert assess(t, at).bumped_at_s == 5.0
    assert assess(t, under).bumped_at_s is None


def test_bump_at_the_end_shorter_than_a_second_is_not_a_bump() -> None:
    t = times(10.0)
    shifts = np.zeros((len(t), 2))
    shifts[t >= 9.5] = (8.0, 0.0)
    result = assess(t, shifts)
    assert result.bumped_at_s is None
    # 2 of 21 samples (9.5 %, over the 5 % tail) at 0.42 %: p95 is 0.42 %.
    assert result.verdict is StabilityVerdict.MOVING


@pytest.mark.parametrize(
    ("p95_fraction", "verdict"),
    [
        (0.001, StabilityVerdict.STABLE),
        (0.00101, StabilityVerdict.SHAKY),
        (0.003, StabilityVerdict.SHAKY),
        (0.00301, StabilityVerdict.MOVING),
    ],
)
def test_verdict_boundaries_are_inclusive(p95_fraction: float, verdict: StabilityVerdict) -> None:
    """Every sample at one magnitude makes p95 exactly that magnitude; samples
    alternate in sign, so nothing sustained is a bump."""
    t = times(10.0)
    magnitude = p95_fraction * LONG_EDGE
    shifts = np.column_stack([magnitude * np.where(np.arange(len(t)) % 2 == 0, 1.0, -1.0), np.zeros(len(t))])
    shifts[0] = (magnitude, 0.0)
    result = assess(t, shifts)
    assert result.p95_shift_fraction == pytest.approx(p95_fraction, rel=1e-12)
    assert result.verdict is verdict


def test_thresholds_are_resolution_free() -> None:
    t = times(20.0)
    rng = np.random.default_rng(SEED)
    shifts = rng.normal(0.0, 1.5, (len(t), 2))
    shifts[0] = 0.0
    at_1920 = assess(t, shifts, 1920)
    at_1280 = assess(t, shifts * (1280 / 1920), 1280)
    assert at_1920.verdict is at_1280.verdict
    assert at_1920.p95_shift_fraction == pytest.approx(at_1280.p95_shift_fraction)


def test_few_features_is_unknown() -> None:
    t = times(10.0)
    shifts = np.zeros((len(t), 2))
    low = assess_camera_stability(t, shifts, counts(len(t), 29), LONG_EDGE)
    assert low == CameraStability(StabilityVerdict.UNKNOWN, None, None, None, len(t))
    enough = assess_camera_stability(t, shifts, counts(len(t), 30), LONG_EDGE)
    assert enough.verdict is StabilityVerdict.STABLE


def test_median_feature_count_decides_unknown() -> None:
    t = times(10.0)
    feature_counts = counts(len(t), 200)
    feature_counts[: len(t) // 2 + 1] = 10
    result = assess_camera_stability(t, np.zeros((len(t), 2)), feature_counts, LONG_EDGE)
    assert result.verdict is StabilityVerdict.UNKNOWN


@pytest.mark.parametrize("n", [0, 1])
def test_too_few_samples_is_unknown(n: int) -> None:
    result = assess_camera_stability(np.zeros(n), np.zeros((n, 2)), counts(n), LONG_EDGE)
    assert result.verdict is StabilityVerdict.UNKNOWN
    assert result.samples == n


def test_bump_right_after_the_reference_is_unknown_not_stable() -> None:
    """Only the reference (zero by construction) precedes it: nothing judged."""
    t = times(10.0)
    shifts = np.zeros((len(t), 2))
    shifts[1:] = (8.0, 0.0)
    assert assess(t, shifts) == CameraStability(StabilityVerdict.UNKNOWN, None, None, None, len(t))
    shifts[1] = 0.0  # one real sample before the bump: judged
    later = assess(t, shifts)
    assert later.verdict is StabilityVerdict.STABLE and later.bumped_at_s == 1.0


def test_failed_samples_are_left_out() -> None:
    t = times(10.0)
    shifts = np.zeros((len(t), 2))
    shifts[3] = np.nan
    result = assess(t, shifts)
    assert result.verdict is StabilityVerdict.STABLE
    assert result.samples == len(t)


def test_bump_found_across_a_failed_sample() -> None:
    t = times(15.0)
    shifts = np.zeros((len(t), 2))
    shifts[t >= 10.0] = (8.0, 0.0)
    shifts[t == 10.5] = np.nan
    assert assess(t, shifts).bumped_at_s == 10.0


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        assess_camera_stability(np.zeros(3), np.zeros((2, 2)), counts(3), LONG_EDGE)
    with pytest.raises(ValueError):
        assess_camera_stability(np.zeros(3), np.zeros((3, 2)), counts(3), 0)
