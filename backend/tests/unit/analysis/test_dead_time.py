"""``app/analysis/dead_time.py``: which stretches of a video may be skipped."""

from __future__ import annotations

import numpy as np

from app.analysis.dead_time import change_share, chunk_spans, keep_spans

DURATION = 100.0
TIMES = [float(t) for t in range(0, 101, 2)]  # a keyframe every 2 s


def shares_moving(*moving: tuple[float, float]) -> list[float]:
    return [0.05 if any(a <= t < b for a, b in moving) else 0.0 for t in TIMES[:-1]]


def test_a_still_middle_is_skipped_and_the_ends_are_kept() -> None:
    spans = keep_spans(TIMES, shares_moving((0, 20), (80, 100)), DURATION)
    assert len(spans) == 2
    assert spans[0][0] == 0.0 and 22.0 <= spans[0][1] <= 23.0  # moving stretch plus its padding
    assert 77.0 <= spans[1][0] <= 78.0 and spans[1][1] == DURATION


def test_movement_anywhere_keeps_that_stretch_with_padding() -> None:
    spans = keep_spans(TIMES, shares_moving((40, 50)), DURATION)
    inside = [s for s in spans if s[0] <= 40.0 and s[1] >= 50.0]
    assert len(inside) == 1 and inside[0][0] <= 37.5 and inside[0][1] >= 52.5


def test_constant_movement_trims_nothing() -> None:
    assert keep_spans(TIMES, [0.05] * (len(TIMES) - 1), DURATION) == [(0.0, DURATION)]


def test_a_short_still_gap_is_not_skipped() -> None:
    spans = keep_spans(TIMES, shares_moving((0, 40), (46, 100)), DURATION)
    assert spans == [(0.0, DURATION)]


def test_samples_too_far_apart_trim_nothing() -> None:
    sparse = [0.0, 10.0, 20.0, 100.0]
    assert keep_spans(sparse, [0.0, 0.0, 0.0], DURATION) == [(0.0, DURATION)]
    assert keep_spans([0.0, 1.0], [0.0], DURATION) == [(0.0, DURATION)]


def test_a_saving_under_the_minimum_is_dropped() -> None:
    # Only 8 s skippable out of 100: not worth the risk.
    spans = keep_spans(TIMES, shares_moving((0, 44), (56, 100)), DURATION)
    assert spans == [(0.0, DURATION)]


def test_kept_spans_are_ordered_and_inside_the_video() -> None:
    spans = keep_spans(TIMES, shares_moving((10, 14), (50, 52), (90, 94)), DURATION)
    assert spans == sorted(spans)
    assert all(0.0 <= a < b <= DURATION for a, b in spans)
    assert all(spans[i][1] < spans[i + 1][0] for i in range(len(spans) - 1))


def test_change_share_counts_changed_pixels_inside_the_court_only() -> None:
    a = np.zeros((10, 10), np.uint8)
    b = a.copy()
    b[0:2, 0:5] = 200  # 10 pixels change
    roi = np.zeros((10, 10), np.uint8)
    roi[:, :] = 255
    assert change_share(a, b, roi) == 0.1
    roi[0:2, :] = 0  # the changed pixels are outside the court
    assert change_share(a, b, roi) == 0.0
    assert change_share(a, b, np.zeros((10, 10), np.uint8)) == 0.0


def test_chunks_cover_the_spans_exactly() -> None:
    spans = [(0.0, 30.0), (60.0, 100.0)]
    pieces = chunk_spans(spans, 4, 6.0)
    assert abs(sum(b - a for a, b in pieces) - 70.0) < 1e-9
    assert pieces[0][0] == 0.0 and pieces[-1][1] == 100.0
    assert all(b - a >= 6.0 for a, b in pieces)
    assert chunk_spans([], 4, 6.0) == []
