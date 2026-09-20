"""Synthetic fixtures for the Stage 12/14/15 unit tests.

Everything here is built from closed-form numpy so the right answer is known by
construction rather than re-derived with the code under test. No video, no
model, no network.

THE CANONICAL SWING
-------------------
``build_sequence()`` returns a 32-frame right-handed forehand whose racket-wrist
x trajectory is, by construction:

* frames 0-7    held at x = +0.2                      -> speed 0, the ready hold
* frames 8-15   cosine ease back to x = -0.5          -> the takeback
* frames 15-31  logistic drive to x = +1.5, whose
                derivative peaks exactly at frame 25  -> forward swing, contact,
                                                         follow-through

so the backward extreme is frame 15, the speed peak is frame 25, and the first
frame reaching 10 % of the peak speed is frame 8. y rises with x, which makes it
a low-to-high (topspin) path.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from app.analysis.normalize import (
    LEFT_ANKLE,
    LEFT_ELBOW,
    LEFT_HIP,
    LEFT_KNEE,
    LEFT_SHOULDER,
    LEFT_WRIST,
    NOSE,
    RIGHT_ANKLE,
    RIGHT_ELBOW,
    RIGHT_HIP,
    RIGHT_KNEE,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
)
from app.models.enums import Handedness, HandednessSource
from app.models.internal import NormalizedSequence
from app.models.responses import ContactDetection, HandednessResult

NUM_LANDMARKS = 33
FRAME_COUNT = 32
FPS = 30.0
CONTACT_FRAME = 25
READY_HOLD_END_FRAME = 7
READY_EXIT_FRAME = 8
TAKEBACK_EXTREME_FRAME = 15
LOGISTIC_K = 2.5

RIGHT_HANDED = HandednessResult(
    handedness=Handedness.RIGHT, confidence=0.95, source=HandednessSource.DETECTED
)
LEFT_HANDED = HandednessResult(
    handedness=Handedness.LEFT, confidence=0.95, source=HandednessSource.DETECTED
)
WEAK_RIGHT_HANDED = HandednessResult(
    handedness=Handedness.RIGHT, confidence=0.20, source=HandednessSource.DETECTED
)
HINTED_RIGHT_HANDED = HandednessResult(
    handedness=Handedness.RIGHT, confidence=0.20, source=HandednessSource.USER_HINT
)
UNKNOWN_HANDED = HandednessResult(
    handedness=Handedness.UNKNOWN, confidence=0.10, source=HandednessSource.DETECTED
)


def canonical_wrist_path(
    *,
    frame_count: int = FRAME_COUNT,
    ready_end: int = READY_HOLD_END_FRAME,
    takeback_end: int = TAKEBACK_EXTREME_FRAME,
    contact_frame: int = CONTACT_FRAME,
    ready_x: float = 0.2,
    takeback_x: float = -0.5,
    finish_x: float = 1.5,
    rise_tu: float = 0.9,
    base_y: float = 0.5,
) -> np.ndarray:
    """(T, 2) racket-wrist trajectory. See the module docstring for the shape."""
    frames = np.arange(frame_count, dtype=np.float64)
    x = np.full((frame_count,), ready_x, dtype=np.float64)
    y = np.full((frame_count,), base_y, dtype=np.float64)
    if frame_count == 0:
        return np.zeros((0, 2), dtype=np.float64)

    span = max(takeback_end - ready_end, 1)
    back = np.arange(ready_end + 1, min(takeback_end + 1, frame_count))
    eased = 0.5 - 0.5 * np.cos(np.pi * (back - ready_end) / span)
    x[back] = ready_x + (takeback_x - ready_x) * eased

    logistic = 1.0 / (1.0 + np.exp(-(frames - contact_frame) / LOGISTIC_K))
    first = float(logistic[min(takeback_end, frame_count - 1)])
    last = float(logistic[frame_count - 1])
    progress = (logistic - first) / max(last - first, 1e-12)
    drive = np.arange(min(takeback_end, frame_count - 1), frame_count)
    x[drive] = takeback_x + (finish_x - takeback_x) * progress[drive]
    y[drive] = base_y + rise_tu * progress[drive]
    return np.stack([x, y], axis=1)


def central_difference(points: np.ndarray, timestamps_s: np.ndarray) -> np.ndarray:
    """Per-landmark central difference on the ACTUAL timestamps (VFR-safe)."""
    if points.shape[0] < 2:
        return np.zeros_like(points)
    return np.asarray(
        np.gradient(points, np.asarray(timestamps_s, dtype=np.float64), axis=0),
        dtype=np.float64,
    )


def build_sequence(
    *,
    frame_count: int = FRAME_COUNT,
    fps: float = FPS,
    timestamps_s: np.ndarray | None = None,
    wrist_xy: np.ndarray | None = None,
    off_wrist_xy: np.ndarray | None = None,
    swing_direction_sign: int = 1,
    visibility: float = 0.9,
    handedness: Handedness = Handedness.RIGHT,
) -> NormalizedSequence:
    """An upright body in the Stage 7 body frame: y UP, mid-hip at (0, 0)."""
    points = np.zeros((frame_count, NUM_LANDMARKS, 2), dtype=np.float64)
    mirror = 1.0 if handedness != Handedness.LEFT else -1.0
    points[:, LEFT_HIP] = (-0.5 * mirror, 0.0)
    points[:, RIGHT_HIP] = (0.5 * mirror, 0.0)
    points[:, LEFT_SHOULDER] = (-0.5 * mirror, 1.0)
    points[:, RIGHT_SHOULDER] = (0.5 * mirror, 1.0)
    points[:, NOSE] = (0.0, 1.5)
    points[:, LEFT_KNEE] = (-0.5 * mirror, -1.0)
    points[:, LEFT_ANKLE] = (-0.5 * mirror, -2.0)
    points[:, RIGHT_KNEE] = (0.5 * mirror, -1.0)
    points[:, RIGHT_ANKLE] = (1.5 * mirror, -1.0)

    racket = RIGHT_WRIST if handedness != Handedness.LEFT else LEFT_WRIST
    off_hand = LEFT_WRIST if racket == RIGHT_WRIST else RIGHT_WRIST
    racket_elbow = RIGHT_ELBOW if racket == RIGHT_WRIST else LEFT_ELBOW
    off_elbow = LEFT_ELBOW if racket == RIGHT_WRIST else RIGHT_ELBOW
    racket_shoulder = RIGHT_SHOULDER if racket == RIGHT_WRIST else LEFT_SHOULDER

    path = (
        canonical_wrist_path(frame_count=frame_count)
        if wrist_xy is None
        else np.asarray(wrist_xy, dtype=np.float64)
    )
    points[:, racket] = path * np.array([mirror, 1.0])
    points[:, off_hand] = (
        np.full((frame_count, 2), (-0.5 * mirror, 0.5))
        if off_wrist_xy is None
        else np.asarray(off_wrist_xy, dtype=np.float64)
    )
    points[:, racket_elbow] = (points[:, racket] + points[:, racket_shoulder]) / 2.0
    points[:, off_elbow] = (-0.5 * mirror, 0.5)

    stamps = (
        np.arange(frame_count, dtype=np.float64) / float(fps)
        if timestamps_s is None
        else np.asarray(timestamps_s, dtype=np.float64)
    )
    velocity = central_difference(points, stamps)
    return NormalizedSequence(
        points=points,
        velocity=velocity,
        acceleration=central_difference(velocity, stamps),
        timestamps_s=stamps,
        valid=np.ones((frame_count,), dtype=bool),
        visibility=np.full((frame_count, NUM_LANDMARKS), visibility, dtype=np.float64),
        swing_direction_sign=int(swing_direction_sign),
        torso_scale_px=120.0,
        width_px=360,
        height_px=640,
        # Subject centred in a 360x640 pose frame, static. y DOWN.
        origin_px=np.tile(np.array([180.0, 320.0]), (frame_count, 1)),
    )


def build_contact(
    frame_index: int = CONTACT_FRAME,
    *,
    confidence: float = 0.8,
    peak_hand_speed_tu_s: float | None = 6.7,
    sanity_flags: list[str] | None = None,
    fps: float = FPS,
) -> ContactDetection:
    return ContactDetection(
        frame_index=int(frame_index),
        time_s=float(frame_index) / fps,
        contact_absolute_time_s=float(frame_index) / fps,
        confidence=float(confidence),
        peak_hand_speed_tu_s=peak_hand_speed_tu_s,
        peak_frame_index=int(frame_index),
        prominence_ratio=2.5,
        sanity_flags=list(sanity_flags or []),
    )


def unusable_contact() -> ContactDetection:
    """The exact shape of ``contact._empty_detection`` (contact.py:505-516)."""
    return ContactDetection(
        frame_index=0,
        time_s=0.0,
        contact_absolute_time_s=0.0,
        confidence=0.0,
        peak_hand_speed_tu_s=None,
        peak_frame_index=0,
        prominence_ratio=0.0,
        sanity_flags=["sequence_unusable"],
    )


def hide(seq: NormalizedSequence, *landmarks: int, value: float = 0.1) -> NormalizedSequence:
    """Drop the named landmarks below VISIBILITY_THRESHOLD for the whole clip."""
    visibility = np.array(seq.visibility, copy=True)
    for landmark in landmarks:
        visibility[:, landmark] = value
    return dataclasses.replace(seq, visibility=visibility)


def hide_frames(
    seq: NormalizedSequence, frames: list[int], *landmarks: int, value: float = 0.1
) -> NormalizedSequence:
    """Drop the named landmarks below the threshold on specific frames only."""
    visibility = np.array(seq.visibility, copy=True)
    for frame in frames:
        for landmark in landmarks:
            visibility[frame, landmark] = value
    return dataclasses.replace(seq, visibility=visibility)


def hide_all(seq: NormalizedSequence, *, value: float = 0.1) -> NormalizedSequence:
    visibility = np.full_like(np.asarray(seq.visibility, dtype=np.float64), value)
    return dataclasses.replace(seq, visibility=visibility)
