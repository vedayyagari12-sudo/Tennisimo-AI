"""Stage 7a/7b: gap detection, interpolation, Savitzky-Golay smoothing.

PURE (PIPELINE.md §4.4, table rows 7a/7b). Arrays in, arrays out. Nothing here
touches the filesystem, the clock or a network.
"""

from __future__ import annotations

from typing import Final

import numpy as np
from scipy.signal import savgol_filter

SAVGOL_WINDOW_LENGTH: Final[int] = 5
SAVGOL_POLYORDER: Final[int] = 2
MAX_GAP_FRAMES: Final[int] = 3


def longest_gap_frames(valid: np.ndarray) -> int:
    """Length of the longest run of invalid frames anywhere in the clip."""
    flags = np.asarray(valid, dtype=bool)
    if flags.ndim != 1:
        raise ValueError("valid must be 1-D")
    longest = 0
    run = 0
    for flag in flags:
        run = 0 if flag else run + 1
        longest = max(longest, run)
    return int(longest)


def interpolate_gaps(
    values: np.ndarray, valid: np.ndarray, *, max_gap_frames: int = MAX_GAP_FRAMES
) -> tuple[np.ndarray, int, int]:
    """Linearly fill invalid frames per coordinate (Stage 7 step 2).

    ``values`` is ``(T, ...)``; interpolation runs along axis 0 independently for
    every trailing coordinate. Leading and trailing gaps are filled by edge
    hold (there is nothing to interpolate between), which is why the caller
    still consults ``longest_gap`` for the ``usable`` decision.

    Returns:
        (filled, interpolated_frame_count, longest_gap_frames). A longest gap
        greater than ``max_gap_frames`` is reported, never raised -- the caller
        turns it into ``PoseQuality.usable=False``.
    """
    data = np.asarray(values, dtype=np.float64)
    flags = np.asarray(valid, dtype=bool)
    if flags.ndim != 1 or data.shape[0] != flags.shape[0]:
        raise ValueError("valid must be 1-D and match values along axis 0")

    longest = longest_gap_frames(flags)
    filled = data.copy()
    if not flags.any():
        return filled, 0, longest

    frames = np.arange(flags.shape[0], dtype=np.float64)
    known = frames[flags]
    flat = filled.reshape(filled.shape[0], -1)
    for column in range(flat.shape[1]):
        flat[:, column] = np.interp(frames, known, flat[flags, column])
    return flat.reshape(filled.shape), int(np.count_nonzero(~flags)), longest


def savgol_smooth(
    values: np.ndarray,
    *,
    window_length: int = SAVGOL_WINDOW_LENGTH,
    polyorder: int = SAVGOL_POLYORDER,
) -> np.ndarray:
    """Savitzky-Golay along axis 0, per coordinate (Stage 7 step 7).

    ``mode="interp"`` is the polynomial edge handling the spec demands (not
    reflection). Sequences shorter than the window are returned unchanged --
    smoothing a 3-frame clip is not meaningful and must not raise.
    """
    data = np.asarray(values, dtype=np.float64)
    if data.shape[0] < window_length:
        return data.copy()
    return np.asarray(
        savgol_filter(
            data, window_length=window_length, polyorder=polyorder, axis=0, mode="interp"
        ),
        dtype=np.float64,
    )


def central_difference(values: np.ndarray, timestamps_s: np.ndarray) -> np.ndarray:
    """Central-difference derivative using the ACTUAL dt (Stage 7 step 8).

    Interior frames use ``(v[i+1] - v[i-1]) / (t[i+1] - t[i-1])``; the two edge
    frames use the one-sided difference. Never assumes 1/30 s.
    """
    data = np.asarray(values, dtype=np.float64)
    times = np.asarray(timestamps_s, dtype=np.float64)
    if times.ndim != 1 or data.shape[0] != times.shape[0]:
        raise ValueError("timestamps_s must be 1-D and match values along axis 0")

    out = np.zeros_like(data)
    count = data.shape[0]
    if count < 2:
        return out

    shape = (-1,) + (1,) * (data.ndim - 1)
    interior_dt = (times[2:] - times[:-2]).reshape(shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:-1] = np.where(interior_dt != 0.0, (data[2:] - data[:-2]) / interior_dt, 0.0)
        first_dt = times[1] - times[0]
        last_dt = times[-1] - times[-2]
        out[0] = (data[1] - data[0]) / first_dt if first_dt != 0.0 else 0.0
        out[-1] = (data[-1] - data[-2]) / last_dt if last_dt != 0.0 else 0.0
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
