"""Pure seam assembly: detection_rate, build_pose_sequence, invariants."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from app.models.internal import PoseSequence, RawPoseSequence
from app.pose.sequence import (
    build_pose_sequence,
    detection_rate,
    validate_sequence_invariants,
)


def make_raw(frames: int = 5, *, detected: np.ndarray | None = None) -> RawPoseSequence:
    rng = np.random.default_rng(0)
    return RawPoseSequence(
        landmarks=rng.random((frames, 33, 4)).astype(np.float32),
        world=rng.random((frames, 33, 3)).astype(np.float32),
        timestamps_s=(np.arange(frames) / 30.0).astype(np.float64),
        detected=np.ones((frames,), dtype=bool) if detected is None else detected,
    )


# --- detection_rate -------------------------------------------------------


def test_detection_rate_empty_is_zero() -> None:
    assert detection_rate(np.zeros((0,), dtype=bool)) == 0.0


def test_detection_rate_all_and_none() -> None:
    assert detection_rate(np.ones((10,), dtype=bool)) == 1.0
    assert detection_rate(np.zeros((10,), dtype=bool)) == 0.0


def test_detection_rate_exactly_at_40_percent_gate() -> None:
    # 4 of 10 == 0.40: the NO_POSE_DETECTED gate is `< 0.40`, so this passes.
    flags = np.array([True] * 4 + [False] * 6, dtype=bool)
    rate = detection_rate(flags)
    assert rate == 0.40
    assert not rate < 0.40


def test_detection_rate_just_below_40_percent_fires_gate() -> None:
    flags = np.array([True] * 39 + [False] * 61, dtype=bool)
    rate = detection_rate(flags)
    assert rate == 0.39
    assert rate < 0.40


def test_detection_rate_just_above_40_percent() -> None:
    flags = np.array([True] * 41 + [False] * 59, dtype=bool)
    rate = detection_rate(flags)
    assert rate == 0.41
    assert not rate < 0.40


def test_detection_rate_rejects_2d() -> None:
    with pytest.raises(ValueError):
        detection_rate(np.zeros((2, 2), dtype=bool))


# --- build_pose_sequence --------------------------------------------------


def test_build_pose_sequence_shapes_and_dtypes() -> None:
    raw = make_raw(7)
    seq = build_pose_sequence(raw, 640, 360)

    assert isinstance(seq, PoseSequence)
    assert seq.landmarks.shape == (7, 33, 4)
    assert seq.landmarks.dtype == np.float32
    assert seq.world.shape == (7, 33, 3)
    assert seq.world.dtype == np.float32
    assert seq.timestamps_s.shape == (7,)
    assert seq.timestamps_s.dtype == np.float64
    assert seq.detected.shape == (7,)
    assert seq.detected.dtype == np.bool_
    assert seq.width_px == 640
    assert seq.height_px == 360


def test_build_pose_sequence_preserves_values() -> None:
    raw = make_raw(3)
    seq = build_pose_sequence(raw, 360, 640)
    np.testing.assert_array_equal(seq.landmarks, raw.landmarks)
    np.testing.assert_array_equal(seq.world, raw.world)
    np.testing.assert_array_equal(seq.timestamps_s, raw.timestamps_s)
    np.testing.assert_array_equal(seq.detected, raw.detected)


def test_build_pose_sequence_is_frozen_and_npz_serializable(tmp_path: Path) -> None:
    seq = build_pose_sequence(make_raw(4), 640, 360)
    assert dataclasses.is_dataclass(seq)
    with pytest.raises(dataclasses.FrozenInstanceError):
        seq.width_px = 1  # type: ignore[misc]

    path = tmp_path / "seq.npz"
    np.savez(path, **dataclasses.asdict(seq))
    loaded = np.load(path)
    np.testing.assert_array_equal(loaded["landmarks"], seq.landmarks)


def test_build_pose_sequence_zero_length_is_valid() -> None:
    seq = build_pose_sequence(make_raw(0), 640, 360)
    assert seq.landmarks.shape == (0, 33, 4)
    validate_sequence_invariants(seq)


def test_build_pose_sequence_rejects_bad_dimensions() -> None:
    with pytest.raises(ValueError):
        build_pose_sequence(make_raw(2), 0, 360)


# --- validate_sequence_invariants ----------------------------------------


def test_validate_accepts_a_well_formed_sequence() -> None:
    validate_sequence_invariants(build_pose_sequence(make_raw(5), 640, 360))


@pytest.mark.parametrize(
    "field,value",
    [
        ("landmarks", np.zeros((5, 33, 5), dtype=np.float32)),
        ("landmarks", np.zeros((5, 17, 4), dtype=np.float32)),
        ("world", np.zeros((5, 33, 4), dtype=np.float32)),
        ("world", np.zeros((4, 33, 3), dtype=np.float32)),
        ("timestamps_s", np.zeros((4,), dtype=np.float64)),
        ("detected", np.zeros((4,), dtype=bool)),
        ("landmarks", np.zeros((5, 33, 4), dtype=np.float64)),
        ("world", np.zeros((5, 33, 3), dtype=np.float64)),
        ("timestamps_s", np.zeros((5,), dtype=np.float32)),
        ("detected", np.zeros((5,), dtype=np.int8)),
    ],
)
def test_validate_rejects_wrong_shape_or_dtype(field: str, value: np.ndarray) -> None:
    good = build_pose_sequence(make_raw(5), 640, 360)
    bad = dataclasses.replace(good, **{field: value})
    with pytest.raises(ValueError):
        validate_sequence_invariants(bad)


def test_validate_rejects_non_positive_dimensions() -> None:
    good = build_pose_sequence(make_raw(5), 640, 360)
    with pytest.raises(ValueError):
        validate_sequence_invariants(dataclasses.replace(good, height_px=0))
