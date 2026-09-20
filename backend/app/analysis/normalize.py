"""Stage 7c: aspect correction, y-flip, mid-hip origin, torso-unit scaling.

PURE (PIPELINE.md 4.4, row 7c). Also hosts the Stage 7 orchestrator
``normalize_sequence``, which applies the nine fixed steps in the order the spec
declares -- the order is load-bearing and must not be rearranged.

The BlazePose landmark indices below belong in ``app/pose/landmarks.py`` once
that module exists (PIPELINE.md 4.4 lists it as pure); they are defined here for
now so that ``app/analysis/`` imports nothing from ``app/pose/``.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.smoothing import (
    MAX_GAP_FRAMES,
    central_difference,
    interpolate_gaps,
    savgol_smooth,
)
from app.models.enums import CameraView
from app.models.internal import NormalizedSequence, PoseSequence
from app.models.responses import PoseQuality

NOSE: Final[int] = 0
LEFT_SHOULDER: Final[int] = 11
RIGHT_SHOULDER: Final[int] = 12
LEFT_ELBOW: Final[int] = 13
RIGHT_ELBOW: Final[int] = 14
LEFT_WRIST: Final[int] = 15
RIGHT_WRIST: Final[int] = 16
LEFT_HIP: Final[int] = 23
RIGHT_HIP: Final[int] = 24
LEFT_KNEE: Final[int] = 25
RIGHT_KNEE: Final[int] = 26
LEFT_ANKLE: Final[int] = 27
RIGHT_ANKLE: Final[int] = 28

CORE_LANDMARKS: Final[tuple[int, ...]] = (
    LEFT_SHOULDER, RIGHT_SHOULDER,
    LEFT_ELBOW, RIGHT_ELBOW,
    LEFT_WRIST, RIGHT_WRIST,
    LEFT_HIP, RIGHT_HIP,
    LEFT_KNEE, RIGHT_KNEE,
    LEFT_ANKLE, RIGHT_ANKLE,
)

VISIBILITY_THRESHOLD: Final[float] = 0.5
CENTROID_JUMP_TU: Final[float] = 0.25
TORSO_CHANGE_FRACTION: Final[float] = 0.35
SUBJECT_IDENTITY_UNSTABLE: Final[str] = "subject_identity_unstable"


def core_visibility(landmarks: np.ndarray) -> np.ndarray:
    """Mean ``visibility`` of the 12 core landmarks, per frame. Shape (T,)."""
    data = np.asarray(landmarks, dtype=np.float64)
    if data.ndim != 3 or data.shape[2] < 4:
        raise ValueError("landmarks must be (T, 33, 4)")
    return np.asarray(data[:, CORE_LANDMARKS, 3].mean(axis=1), dtype=np.float64)


def valid_frames(
    landmarks: np.ndarray,
    detected: np.ndarray,
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> np.ndarray:
    """Stage 7 step 1: valid if detected AND mean core visibility >= threshold."""
    flags = np.asarray(detected, dtype=bool)
    return flags & (core_visibility(landmarks) >= threshold)


def apply_aspect_correction(xy: np.ndarray, width_px: int, height_px: int) -> np.ndarray:
    """Stage 7 step 3: multiply x by width/height so one x unit == one y unit."""
    if width_px <= 0 or height_px <= 0:
        raise ValueError("width_px and height_px must be positive")
    data = np.array(xy, dtype=np.float64, copy=True)
    data[..., 0] *= float(width_px) / float(height_px)
    return data


def flip_y(xy: np.ndarray) -> np.ndarray:
    """Stage 7 step 4: negate y so up is positive."""
    data = np.array(xy, dtype=np.float64, copy=True)
    data[..., 1] *= -1.0
    return data


def mid_hip(xy: np.ndarray) -> np.ndarray:
    """Per-frame mid-hip point, shape (T, 2)."""
    data = np.asarray(xy, dtype=np.float64)
    return (data[:, LEFT_HIP, :] + data[:, RIGHT_HIP, :]) / 2.0


def mid_shoulder(xy: np.ndarray) -> np.ndarray:
    """Per-frame mid-shoulder point, shape (T, 2)."""
    data = np.asarray(xy, dtype=np.float64)
    return (data[:, LEFT_SHOULDER, :] + data[:, RIGHT_SHOULDER, :]) / 2.0


def origin_to_pixels(origin_flipped: np.ndarray, height_px: int) -> np.ndarray:
    """Undo steps 3 and 4 on the mid-hip so it is expressed in image pixels.

    ``origin_flipped`` is the per-frame mid-hip AFTER ``apply_aspect_correction``
    and ``flip_y``, i.e. what step 5 subtracts. Raw landmarks are normalized
    [0, 1] image coordinates with y DOWN, so::

        raw_x = origin_flipped.x / (width_px / height_px)
        raw_y = -origin_flipped.y

    and multiplying by (width_px, height_px) gives pixels. The x factor
    collapses: ``raw_x * width_px == origin_flipped.x * height_px``, so
    ``width_px`` does not appear and cannot be passed in wrong.

    Returns a (T, 2) float64 array in pose-stream pixels, y DOWN.
    """
    if height_px <= 0:
        raise ValueError("height_px must be positive")
    data = np.asarray(origin_flipped, dtype=np.float64).reshape(-1, 2)
    out = np.empty_like(data)
    out[:, 0] = data[:, 0] * float(height_px)
    out[:, 1] = -data[:, 1] * float(height_px)
    return out


def to_body_frame(xy: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """Stage 7 step 5: subtract the per-frame origin from every landmark."""
    data = np.asarray(xy, dtype=np.float64)
    return data - np.asarray(origin, dtype=np.float64)[:, None, :]


def torso_lengths(xy: np.ndarray) -> np.ndarray:
    """Per-frame |mid_shoulder - mid_hip|, shape (T,)."""
    return np.asarray(
        np.linalg.norm(mid_shoulder(xy) - mid_hip(xy), axis=1), dtype=np.float64
    )


def torso_scale(xy: np.ndarray, valid: np.ndarray | None = None) -> float:
    """Stage 7 step 6: one TU = the MEDIAN over frames of the torso length.

    Median, not per-frame: a per-frame scale would inject the foreshortening of
    the torso into every derived velocity.
    """
    lengths = torso_lengths(xy)
    if valid is not None:
        flags = np.asarray(valid, dtype=bool)
        if flags.any():
            lengths = lengths[flags]
    lengths = lengths[lengths > 0.0]
    if lengths.size == 0:
        return 0.0
    return float(np.median(lengths))


def subject_identity_unstable(
    xy: np.ndarray,
    valid: np.ndarray,
    torso_scale_units: float,
    *,
    centroid_jump_tu: float = CENTROID_JUMP_TU,
    torso_change_fraction: float = TORSO_CHANGE_FRACTION,
) -> bool:
    """Stage 7 step 1b: detect MediaPipe re-anchoring onto a different person.

    Between consecutive DETECTED frames, a mid-hip centroid jump above
    ``centroid_jump_tu`` or a torso-length change above
    ``torso_change_fraction`` means the landmarks stopped describing the same
    body. Fails soft: the caller only appends a flag.
    """
    flags = np.asarray(valid, dtype=bool)
    indices = np.flatnonzero(flags)
    if indices.size < 2 or torso_scale_units <= 0.0:
        return False

    data = np.asarray(xy, dtype=np.float64)
    centroids = mid_hip(data)[indices]
    lengths = torso_lengths(data)[indices]

    jumps = np.linalg.norm(np.diff(centroids, axis=0), axis=1) / torso_scale_units
    if np.any(jumps > centroid_jump_tu):
        return True

    previous = lengths[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        changes = np.abs(np.diff(lengths)) / np.where(previous > 0.0, previous, np.nan)
    return bool(np.any(np.nan_to_num(changes, nan=0.0) > torso_change_fraction))


def landmark_visible(
    visibility: np.ndarray, *, threshold: float = VISIBILITY_THRESHOLD
) -> np.ndarray:
    """Per-frame reliability of ONE landmark's position. Shape (T,) bool.

    Stage 7 step 1's gate is the MEAN over the 12 core landmarks, so a frame can
    pass overall while one specific landmark is occluded, motion-blurred or
    hallucinated behind the body. Any metric that integrates a single landmark
    over time needs this per-landmark view instead.
    """
    data = np.asarray(visibility, dtype=np.float64)
    return np.asarray(data >= float(threshold), dtype=bool)


def path_length(
    points: np.ndarray,
    visibility: np.ndarray | None = None,
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> float:
    """Integrated path length sum of |dp| over a (T, 2) trajectory.

    When ``visibility`` (the (T,) column for THIS landmark) is supplied, a step
    is counted only if the landmark was individually visible at BOTH of its
    endpoints. One occluded frame places the landmark somewhere it never was,
    and the accumulation charges that fictional excursion twice -- in and back
    out -- which is large enough to invert a comparison between two landmarks.
    Excluding the step is the only honest option: the distance actually
    travelled while unobserved is unknown, and guessing it is what produced the
    wrong answer in the first place.
    """
    data = np.asarray(points, dtype=np.float64)
    if data.shape[0] < 2:
        return 0.0
    steps = np.linalg.norm(np.diff(data, axis=0), axis=1)
    if visibility is not None:
        visible = landmark_visible(visibility, threshold=threshold)
        steps = steps[visible[1:] & visible[:-1]]
    return float(np.sum(steps))


def speed_series(points: np.ndarray, timestamps_s: np.ndarray) -> np.ndarray:
    """Scalar speed of a (T, 2) trajectory, using the actual dt."""
    velocity = central_difference(np.asarray(points, dtype=np.float64), timestamps_s)
    return np.asarray(np.linalg.norm(velocity, axis=1), dtype=np.float64)


def swing_direction_sign(wrist_points: np.ndarray, timestamps_s: np.ndarray) -> int:
    """Stage 7 step 9: sign of racket-hand x-displacement, takeback-end to contact.

    Contact and takeback-end are Stage 9/12 products, so this uses the
    kinematic proxies for them, neither of which needs a sign to compute:
    contact ~ the peak-speed frame, takeback-end ~ the slowest frame before it
    (the hand pauses at the end of the takeback). Zero displacement gives +1.
    """
    points = np.asarray(wrist_points, dtype=np.float64)
    if points.shape[0] < 2:
        return 1
    speed = speed_series(points, timestamps_s)
    peak = int(np.argmax(speed))
    takeback_end = int(np.argmin(speed[: peak + 1])) if peak > 0 else 0
    displacement = float(points[peak, 0] - points[takeback_end, 0])
    return 1 if displacement >= 0.0 else -1


def normalize_sequence(
    seq: PoseSequence, *, max_gap_frames: int = MAX_GAP_FRAMES
) -> tuple[NormalizedSequence, PoseQuality]:
    """Stage 7, all nine steps, in the declared order. Never raises on bad data.

    Step 1b needs a TU scale before step 6 formally computes one, so the torso
    median is evaluated once on the aspect-corrected coordinates and the SAME
    scalar is reused at step 6; the visible order of operations is unchanged.
    """
    landmarks = np.asarray(seq.landmarks, dtype=np.float64)
    frame_count = int(landmarks.shape[0])
    timestamps_s = np.asarray(seq.timestamps_s, dtype=np.float64)
    visibility = (
        landmarks[:, :, 3].copy() if frame_count else np.zeros((0, 33), dtype=np.float64)
    )

    # 1. visibility gate
    valid = (
        valid_frames(landmarks, seq.detected)
        if frame_count
        else np.zeros((0,), dtype=bool)
    )
    raw_xy = landmarks[:, :, :2]

    # 3. aspect correction, hoisted ahead of the TU-denominated step-1b check so
    #    that the distances compared in step 1b are isotropic.
    corrected = apply_aspect_correction(raw_xy, seq.width_px, seq.height_px)
    scale_units = torso_scale(corrected, valid)

    # 1b. subject-continuity check
    flags: list[str] = []
    if subject_identity_unstable(corrected, valid, scale_units):
        flags.append(SUBJECT_IDENTITY_UNSTABLE)

    # 2. gap interpolation
    filled, interpolated_frames, longest_gap = interpolate_gaps(
        corrected, valid, max_gap_frames=max_gap_frames
    )
    usable = bool(
        frame_count > 0
        and bool(valid.any())
        and longest_gap <= max_gap_frames
        and scale_units > 0.0
    )

    # 4. flip y, 5. mid-hip origin, 6. torso-unit scaling
    flipped = flip_y(filled)
    origin = mid_hip(flipped)
    body = to_body_frame(flipped, origin)
    scaled = body / scale_units if scale_units > 0.0 else body

    # 7. smoothing, 8. derivatives
    points = savgol_smooth(scaled)
    velocity = central_difference(points, timestamps_s)
    acceleration = central_difference(velocity, timestamps_s)

    # 9. swing direction sign, from the longer-path wrist (handedness is Stage 8)
    if frame_count >= 2:
        right_path = path_length(points[:, RIGHT_WRIST, :])
        left_path = path_length(points[:, LEFT_WRIST, :])
        proxy = RIGHT_WRIST if right_path >= left_path else LEFT_WRIST
        sign = swing_direction_sign(points[:, proxy, :], timestamps_s)
    else:
        sign = 1

    torso_scale_px = float(scale_units * float(seq.height_px))
    normalized = NormalizedSequence(
        origin_px=origin_to_pixels(origin, seq.height_px),
        points=points,
        velocity=velocity,
        acceleration=acceleration,
        timestamps_s=timestamps_s,
        valid=valid,
        visibility=np.asarray(visibility, dtype=np.float64),
        swing_direction_sign=int(sign),
        torso_scale_px=torso_scale_px,
        width_px=int(seq.width_px),
        height_px=int(seq.height_px),
    )

    frames_with_pose = int(np.count_nonzero(valid))
    mean_visibility = (
        float(np.clip(core_visibility(landmarks)[valid].mean(), 0.0, 1.0))
        if frames_with_pose
        else 0.0
    )
    quality = PoseQuality(
        frames_with_pose=frames_with_pose,
        frames_missing=int(frame_count - frames_with_pose),
        detection_rate=(frames_with_pose / frame_count) if frame_count else 0.0,
        mean_visibility=mean_visibility,
        longest_gap_frames=int(longest_gap) if frame_count else 0,
        interpolated_frames=int(interpolated_frames),
        torso_scale_px=torso_scale_px,
        estimated_camera_view=CameraView.UNKNOWN,
        usable=usable,
        flags=flags,
    )
    return normalized, quality
