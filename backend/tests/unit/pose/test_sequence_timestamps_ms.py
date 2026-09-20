"""Stage 6.5 timestamp rule: rounded, strictly-increasing int ms, never stored."""

from __future__ import annotations

import numpy as np
import pytest

from app.models.internal import RawPoseSequence
from app.pose.sequence import build_pose_sequence, frame_timestamps_ms


def test_ascending_float_pts_gives_ascending_int_ms() -> None:
    pts = np.array([10.0, 10.0333, 10.0667, 10.1], dtype=np.float64)
    out = frame_timestamps_ms(pts, 10.0)

    assert out.dtype == np.int64
    assert out.tolist() == [0, 33, 67, 100]
    assert np.all(np.diff(out) > 0)


def test_duplicate_pts_still_strictly_increasing() -> None:
    # Stage 5's duplicate-frame selection on a 24 fps source.
    pts = np.array([2.0, 2.0, 2.0, 2.0416], dtype=np.float64)
    out = frame_timestamps_ms(pts, 2.0)

    assert out.tolist() == [0, 1, 2, 42]
    assert np.all(np.diff(out) > 0)


def test_near_duplicate_pts_still_strictly_increasing() -> None:
    pts = np.array([0.0, 0.0001, 0.0002, 0.0003], dtype=np.float64)
    out = frame_timestamps_ms(pts, 0.0)

    assert out.tolist() == [0, 1, 2, 3]
    assert np.all(np.diff(out) > 0)


def test_previous_ms_continues_a_streaming_sequence() -> None:
    # The streaming caller in extractor.py emits one frame at a time and must
    # produce exactly the same values as the vectorised call.
    pts = np.array([5.0, 5.0, 5.05], dtype=np.float64)
    batch = frame_timestamps_ms(pts, 5.0)

    streamed: list[int] = []
    previous = -1
    for value in pts:
        ms = int(
            frame_timestamps_ms(
                np.asarray([value], dtype=np.float64), 5.0, previous_ms=previous
            )[0]
        )
        streamed.append(ms)
        previous = ms

    assert streamed == batch.tolist()


def test_rounded_ms_never_reach_the_seam_timestamps() -> None:
    pts = np.array([1.0004, 1.0337, 1.0671], dtype=np.float64)
    raw = RawPoseSequence(
        landmarks=np.zeros((3, 33, 4), dtype=np.float32),
        world=np.zeros((3, 33, 3), dtype=np.float32),
        timestamps_s=pts,
        detected=np.ones((3,), dtype=bool),
    )
    seq = build_pose_sequence(raw, 640, 360)
    ms = frame_timestamps_ms(pts, 1.0004)

    # Full precision survives, bit for bit.
    assert seq.timestamps_s.dtype == np.float64
    np.testing.assert_array_equal(seq.timestamps_s, pts)
    # And the seam holds none of the quantised values, in any scaling.
    assert not np.any(np.isin(seq.timestamps_s, ms.astype(np.float64)))
    assert not np.any(np.isin((seq.timestamps_s * 1000.0).astype(np.int64), ms))


def test_non_1d_input_rejected() -> None:
    with pytest.raises(ValueError):
        frame_timestamps_ms(np.zeros((2, 2), dtype=np.float64), 0.0)
