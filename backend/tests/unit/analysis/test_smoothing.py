"""Stage 7a/7b unit tests (PIPELINE.md 4.5). Synthetic data only."""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.smoothing import (
    MAX_GAP_FRAMES,
    SAVGOL_POLYORDER,
    SAVGOL_WINDOW_LENGTH,
    central_difference,
    interpolate_gaps,
    longest_gap_frames,
    savgol_smooth,
)


def test_savgol_window_is_pinned_to_five() -> None:
    # Stage 7 step 7: the window dropped 7 -> 5 with the Tasks API migration
    # because MediaPipe now low-passes upstream. A silent revert must fail here.
    assert SAVGOL_WINDOW_LENGTH == 5
    assert SAVGOL_POLYORDER == 2
    assert MAX_GAP_FRAMES == 3


def test_longest_gap_frames_counts_the_longest_invalid_run() -> None:
    valid = np.array([True, False, False, True, False, False, False, False, True])
    assert longest_gap_frames(valid) == 4
    assert longest_gap_frames(np.ones(5, dtype=bool)) == 0


def test_interpolate_gaps_recovers_a_linear_ramp_exactly() -> None:
    ramp = np.stack([np.arange(10.0), 3.0 * np.arange(10.0)], axis=1)
    valid = np.ones(10, dtype=bool)
    valid[[3, 4, 7]] = False
    punched = ramp.copy()
    punched[~valid] = -999.0

    filled, interpolated, longest = interpolate_gaps(punched, valid)

    assert np.allclose(filled, ramp)
    assert interpolated == 3
    assert longest == 2


def test_interpolate_gaps_reports_a_gap_longer_than_three_without_raising() -> None:
    values = np.arange(12.0).reshape(12, 1)
    valid = np.ones(12, dtype=bool)
    valid[4:9] = False  # 5-frame gap
    filled, _, longest = interpolate_gaps(values, valid)
    assert longest == 5 > MAX_GAP_FRAMES
    assert np.all(np.isfinite(filled))


def test_interpolate_gaps_with_no_valid_frames_is_a_no_op() -> None:
    values = np.zeros((4, 2))
    filled, interpolated, longest = interpolate_gaps(values, np.zeros(4, dtype=bool))
    assert interpolated == 0
    assert longest == 4
    assert np.allclose(filled, values)


def test_savgol_smooth_preserves_amplitude_and_peak_index() -> None:
    rng = np.random.default_rng(7)
    frames = np.arange(90.0)
    # One unambiguous peak (a swing-speed bump) plus noise, so argmax is a
    # meaningful target.
    clean = np.exp(-(((frames - 40.0) / 6.0) ** 2)) * np.sin(np.pi * frames / 89.0)
    noisy = clean + rng.normal(0.0, 0.02, size=clean.shape)

    smoothed = savgol_smooth(noisy.reshape(-1, 1))[:, 0]

    assert abs(smoothed.max() - clean.max()) < 0.05
    assert abs(int(np.argmax(smoothed)) - int(np.argmax(clean))) <= 1
    # Noise really was reduced.
    assert np.std(smoothed - clean) < np.std(noisy - clean)

    # A moving average of the same length fails the peak test that SG passes:
    # it attenuates, which is exactly why Stage 7 step 7 rejects it.
    kernel = np.ones(SAVGOL_WINDOW_LENGTH) / SAVGOL_WINDOW_LENGTH
    averaged = np.convolve(noisy, kernel, mode="same")
    assert abs(averaged.max() - clean.max()) > abs(smoothed.max() - clean.max())


def test_savgol_smooth_leaves_a_polynomial_untouched_including_the_edges() -> None:
    frames = np.arange(20.0)
    quadratic = (2.0 * frames**2 - 5.0 * frames + 1.0).reshape(-1, 1)
    # polyorder=2 with polynomial edge handling reproduces a quadratic exactly,
    # edges included; reflection-based edge handling would not.
    assert np.allclose(savgol_smooth(quadratic), quadratic)


def test_savgol_smooth_returns_short_sequences_unchanged() -> None:
    short = np.arange(9.0).reshape(3, 3)
    assert np.allclose(savgol_smooth(short), short)


def test_central_difference_is_exact_on_non_uniform_timestamps() -> None:
    # Catches the assumed-dt bug: dt here is never 1/30.
    timestamps = np.array([0.0, 0.017, 0.061, 0.080, 0.133])
    velocity = np.array([4.0, -1.5])
    positions = timestamps[:, None] * velocity[None, :]

    derivative = central_difference(positions, timestamps)

    assert np.allclose(derivative, np.broadcast_to(velocity, positions.shape))
    # Assuming 1/30 s would be wrong everywhere except by luck.
    assumed = np.gradient(positions, 1.0 / 30.0, axis=0)
    assert not np.allclose(assumed, derivative)


def test_central_difference_degenerate_inputs() -> None:
    assert central_difference(np.zeros((1, 2)), np.zeros(1)).shape == (1, 2)
    repeated = central_difference(np.ones((3, 1)), np.zeros(3))
    assert np.all(np.isfinite(repeated))
    with pytest.raises(ValueError):
        central_difference(np.zeros((3, 2)), np.zeros(4))
