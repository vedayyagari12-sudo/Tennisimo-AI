"""Seam assembly for Stage 6 (PIPELINE.md Stage 6.8, 6.9, 4.1).

PURE: numpy only. ``mediapipe`` is banned from this module -- the seam builder
must be testable and runnable with MediaPipe absent from the environment
entirely (PIPELINE.md 4.4). The millisecond-timestamp derivation lives here
rather than in ``extractor.py`` specifically so that it is pure and unit-tested.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.models.internal import PoseSequence, RawPoseSequence

NUM_LANDMARKS: Final[int] = 33


def frame_timestamps_ms(
    timestamps_s: np.ndarray,
    window_start_s: float,
    *,
    previous_ms: int = -1,
) -> np.ndarray:
    """Integer milliseconds relative to the window start, strictly increasing.

    ``detect_for_video()`` REQUIRES strict monotonicity. At a 30 fps target the
    natural spacing is ~33 ms so the clamp should never fire, but Stage 5's
    duplicate-frame selection on 24 fps sources makes the guarantee worth
    enforcing rather than assuming.

    The returned values are fed to MediaPipe and DISCARDED -- they never reach
    ``PoseSequence.timestamps_s``, which keeps full-precision PTS.

    ``previous_ms`` is the last millisecond value already emitted for this
    sequence (default ``-1``, meaning "none yet"). It exists so the streaming
    caller in ``extractor.py`` can apply this exact rule one frame at a time.
    """
    values = np.asarray(timestamps_s, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("timestamps_s must be 1-D")

    out = np.empty(values.shape, dtype=np.int64)
    prior = int(previous_ms)
    for index, timestamp_s in enumerate(values):
        rounded = int(round((float(timestamp_s) - float(window_start_s)) * 1000.0))
        clamped = max(prior + 1, rounded)
        out[index] = clamped
        prior = clamped
    return out


def detection_rate(detected: np.ndarray) -> float:
    """Fraction of frames with ``detected=True``. Empty input is 0.0."""
    flags = np.asarray(detected, dtype=bool)
    if flags.ndim != 1:
        raise ValueError("detected must be 1-D")
    if flags.size == 0:
        return 0.0
    return float(np.count_nonzero(flags)) / float(flags.size)


def build_pose_sequence(
    raw: RawPoseSequence, width_px: int, height_px: int
) -> PoseSequence:
    """Assemble the seam object from Stage 6's raw output plus frame dimensions."""
    if width_px <= 0 or height_px <= 0:
        raise ValueError("width_px and height_px must be positive")

    sequence = PoseSequence(
        landmarks=np.ascontiguousarray(raw.landmarks, dtype=np.float32),
        world=np.ascontiguousarray(raw.world, dtype=np.float32),
        timestamps_s=np.ascontiguousarray(raw.timestamps_s, dtype=np.float64),
        detected=np.ascontiguousarray(raw.detected, dtype=bool),
        width_px=int(width_px),
        height_px=int(height_px),
    )
    validate_sequence_invariants(sequence)
    return sequence


def validate_sequence_invariants(seq: PoseSequence) -> None:
    """Sanity-check the seam object's shapes and dtypes.

    Raises:
        ValueError: if any shape, dtype or length invariant is violated.
    """
    frame_count = seq.landmarks.shape[0]

    if seq.landmarks.shape != (frame_count, NUM_LANDMARKS, 4):
        raise ValueError(
            f"landmarks must be (T, {NUM_LANDMARKS}, 4), got {seq.landmarks.shape}"
        )
    if seq.world.shape != (frame_count, NUM_LANDMARKS, 3):
        raise ValueError(
            f"world must be (T, {NUM_LANDMARKS}, 3), got {seq.world.shape}"
        )
    if seq.timestamps_s.shape != (frame_count,):
        raise ValueError(
            f"timestamps_s must be (T,), got {seq.timestamps_s.shape}"
        )
    if seq.detected.shape != (frame_count,):
        raise ValueError(f"detected must be (T,), got {seq.detected.shape}")

    if seq.landmarks.dtype != np.float32:
        raise ValueError(f"landmarks must be float32, got {seq.landmarks.dtype}")
    if seq.world.dtype != np.float32:
        raise ValueError(f"world must be float32, got {seq.world.dtype}")
    if seq.timestamps_s.dtype != np.float64:
        raise ValueError(
            f"timestamps_s must be float64, got {seq.timestamps_s.dtype}"
        )
    if seq.detected.dtype != np.bool_:
        raise ValueError(f"detected must be bool, got {seq.detected.dtype}")

    if not isinstance(seq.width_px, int) or not isinstance(seq.height_px, int):
        raise ValueError("width_px and height_px must be ints")
    if seq.width_px <= 0 or seq.height_px <= 0:
        raise ValueError("width_px and height_px must be positive")
