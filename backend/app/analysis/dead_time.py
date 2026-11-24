"""Dead-time trimming for the ball pass. PURE: numbers in, numbers out.

A phone video of a session holds long stretches where nothing happens on the
court (waiting, changeovers, a paused recording). The ball pass spends the same
time on those as on a rally and finds nothing. This module decides which
stretches may be skipped, from how much of the court picture changes between
samples a second or so apart.

It is deliberately cautious. A stretch is skipped only when NOTHING on the
court moved for a long time (``MIN_SKIP_S``), and every kept stretch is padded,
so a serve that starts after a pause keeps its run-up. A player walking
between points is movement and is kept; the saving is on footage that is
truly still. Skipped time is time with no candidate, which the later stages
already treat as a quiet gap, so the result for the kept time is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np
import numpy.typing as npt

#: A sample pair is "moving" when more than this share of the court region's
#: pixels changed by more than ``PIXEL_DELTA``.
CHANGE_SHARE: Final[float] = 0.0015
PIXEL_DELTA: Final[int] = 20
#: Kept around every moving stretch, seconds.
PAD_S: Final[float] = 2.5
#: A still stretch shorter than this (after padding) is not worth skipping.
MIN_SKIP_S: Final[float] = 6.0
#: Fewer samples than this per ``MIN_SKIP_S`` means the samples are too far
#: apart to tell "still" from "moved and came back": trim nothing.
MAX_SAMPLE_GAP_S: Final[float] = 3.0
#: Trimming is dropped unless it saves at least this share of the video.
MIN_SAVING_SHARE: Final[float] = 0.10


def change_share(
    previous: npt.NDArray[np.uint8], current: npt.NDArray[np.uint8], roi: npt.NDArray[np.uint8]
) -> float:
    """Share of the court region's pixels that changed between two same-sized
    grey pictures. ``roi`` is 0/255 with the same shape."""
    inside = roi > 0
    count = int(inside.sum())
    if count == 0:
        return 0.0
    delta = np.abs(previous.astype(np.int16) - current.astype(np.int16))
    return float(((delta > PIXEL_DELTA) & inside).sum()) / count


def keep_spans(
    times_s: Sequence[float],
    shares: Sequence[float],
    duration_s: float,
    *,
    threshold: float = CHANGE_SHARE,
    pad_s: float = PAD_S,
    min_skip_s: float = MIN_SKIP_S,
) -> list[tuple[float, float]]:
    """The time spans to analyse. ``shares[i]`` is the change between sample
    ``i`` and sample ``i+1`` (so there is one fewer share than times).

    Returns the whole video ``[(0, duration_s)]`` when trimming is not safe or
    not worth it."""
    whole = [(0.0, duration_s)]
    if len(times_s) < 3 or len(shares) != len(times_s) - 1 or duration_s <= 0.0:
        return whole
    if max(np.diff(np.asarray(times_s, dtype=np.float64))) > MAX_SAMPLE_GAP_S:
        return whole
    moving = [(times_s[i], times_s[i + 1]) for i, s in enumerate(shares) if s > threshold]
    # The video's own start and end are always kept: the court is found on the
    # first frames, and the last stretch is cheap to keep.
    moving.append((0.0, min(duration_s, pad_s)))
    moving.append((max(0.0, duration_s - pad_s), duration_s))
    moving.sort()
    merged: list[tuple[float, float]] = []
    for start, end in moving:
        start, end = max(0.0, start - pad_s), min(duration_s, end + pad_s)
        if merged and start <= merged[-1][1] + min_skip_s:
            # The gap to the previous kept span is too short to be worth a skip.
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    kept = sum(end - start for start, end in merged)
    if duration_s - kept < MIN_SAVING_SHARE * duration_s:
        return whole
    return merged


def chunk_spans(
    spans: Sequence[tuple[float, float]], workers: int, min_chunk_s: float
) -> list[tuple[float, float]]:
    """Cut ``spans`` into pieces for a pool of ``workers``: about two pieces a
    worker, none shorter than ``min_chunk_s`` (unless the span itself is), so
    the pool keeps every worker busy to the end."""
    total = sum(end - start for start, end in spans)
    if total <= 0.0:
        return []
    size = max(min_chunk_s, total / (max(workers, 1) * 2))
    pieces: list[tuple[float, float]] = []
    for start, end in spans:
        count = max(1, int((end - start) // size))
        step = (end - start) / count
        pieces.extend((start + step * i, start + step * (i + 1)) for i in range(count))
    return pieces


__all__ = ["CHANGE_SHARE", "change_share", "chunk_spans", "keep_spans"]
