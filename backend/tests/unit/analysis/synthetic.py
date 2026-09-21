"""Programmatic fixtures for the Stage 7-9 unit tests.

No video, no model, no network: every object here is built from numpy arrays so
the pure layer can be exercised exactly as PIPELINE.md 4.5 requires.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from app.analysis.normalize import (
    LEFT_ELBOW,
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_ELBOW,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
)
from app.models.enums import CameraView, Handedness
from app.models.internal import NormalizedSequence, PoseSequence
from app.models.responses import PoseQuality

NUM_LANDMARKS = 33


def triangular_speed(
    frame_count: int, peak_frame: int, peak_speed: float, *, baseline: float = 0.5
) -> np.ndarray:
    """A single-peak speed profile: linear rise to ``peak_frame``, linear fall after."""
    frames = np.arange(frame_count, dtype=np.float64)
    rise = baseline + (peak_speed - baseline) * frames / max(peak_frame, 1)
    fall_span = max(frame_count - 1 - peak_frame, 1)
    fall = peak_speed - (peak_speed - baseline) * (frames - peak_frame) / fall_span
    return np.where(frames <= peak_frame, rise, fall)


def arc_positions(
    frame_count: int,
    extension_peak_frame: int,
    *,
    extension_width_frames: float = 6.0,
    min_extension_tu: float = 0.5,
    max_extension_tu: float = 1.0,
    shoulder_xy: tuple[float, float] = (0.2, 1.0),
    phi_span_rad: float = 1.2,
    direction: float = 1.0,
) -> np.ndarray:
    """Wrist positions on an arc about the shoulder, most extended at one frame.

    This is what a swing actually looks like to the Stage 9 gates: the wrist
    sweeps from behind the hips to in front of them, and the wrist-to-shoulder
    distance peaks at ``extension_peak_frame`` rather than at the clip end.
    """
    frames = np.arange(frame_count, dtype=np.float64)
    phi = np.linspace(-phi_span_rad, phi_span_rad, frame_count)
    extension = min_extension_tu + (max_extension_tu - min_extension_tu) * np.exp(
        -(((frames - float(extension_peak_frame)) / extension_width_frames) ** 2)
    )
    x = shoulder_xy[0] + direction * extension * np.sin(phi)
    y = shoulder_xy[1] - extension * np.cos(phi)
    return np.stack([x, y], axis=1)


def make_normalized_sequence(
    speed: np.ndarray,
    *,
    fps: float = 30.0,
    wrist_positions: np.ndarray | None = None,
    swing_direction_sign: int = 1,
    handedness: Handedness = Handedness.RIGHT,
    off_hand_speed_scale: float = 0.05,
    start_time_s: float = 0.0,
) -> NormalizedSequence:
    """A NormalizedSequence whose racket-hand speed is EXACTLY ``speed``.

    ``velocity`` is a field of the seam object, so supplying the profile
    directly keeps the Stage 9 assertions analytic instead of re-deriving them
    from the positions with the same code under test. Positions are the
    arc in ``arc_positions`` unless overridden.
    """
    profile = np.asarray(speed, dtype=np.float64)
    count = int(profile.shape[0])
    dt = 1.0 / float(fps)
    timestamps_s = start_time_s + np.arange(count, dtype=np.float64) * dt

    direction = float(swing_direction_sign)
    racket = RIGHT_WRIST if handedness != Handedness.LEFT else LEFT_WRIST
    off_hand = LEFT_WRIST if racket == RIGHT_WRIST else RIGHT_WRIST
    shoulder = RIGHT_SHOULDER if racket == RIGHT_WRIST else LEFT_SHOULDER
    elbow = RIGHT_ELBOW if racket == RIGHT_WRIST else LEFT_ELBOW

    # A left-handed player is the mirror image, so the racket shoulder sits at
    # +0.2 either way and the arc geometry below is handedness-agnostic.
    mirror = 1.0 if racket == RIGHT_WRIST else -1.0

    if wrist_positions is None:
        wrist_xy = (
            arc_positions(
                count,
                int(np.argmax(profile)) + 2,
                shoulder_xy=(0.2, 1.0),
                direction=direction,
            )
            if count
            else np.zeros((0, 2), dtype=np.float64)
        )
    else:
        wrist_xy = np.asarray(wrist_positions, dtype=np.float64)

    points = np.zeros((count, NUM_LANDMARKS, 2), dtype=np.float64)
    points[:, LEFT_HIP] = (-0.15 * mirror, 0.0)
    points[:, RIGHT_HIP] = (0.15 * mirror, 0.0)
    points[:, LEFT_SHOULDER] = (-0.2 * mirror, 1.0)
    points[:, RIGHT_SHOULDER] = (0.2 * mirror, 1.0)
    points[:, racket] = wrist_xy
    points[:, elbow] = (wrist_xy + points[:, shoulder]) / 2.0
    points[:, off_hand] = np.stack(
        [np.full(count, -0.1 * direction * mirror), np.full(count, 0.5)], axis=1
    )

    velocity = np.zeros_like(points)
    velocity[:, racket, 0] = direction * profile
    velocity[:, off_hand, 0] = direction * profile * off_hand_speed_scale
    acceleration = np.zeros_like(points)
    if count >= 2:
        acceleration[1:-1] = (velocity[2:] - velocity[:-2]) / (2.0 * dt)

    return NormalizedSequence(
        points=points,
        velocity=velocity,
        acceleration=acceleration,
        timestamps_s=timestamps_s,
        valid=np.ones((count,), dtype=bool),
        visibility=np.full((count, NUM_LANDMARKS), 0.9, dtype=np.float64),
        swing_direction_sign=int(swing_direction_sign),
        torso_scale_px=120.0,
        width_px=360,
        height_px=640,
        # Subject centred in a 360x640 pose frame, static. y DOWN.
        origin_px=np.tile(np.array([180.0, 320.0]), (count, 1)),
    )


def make_pose_quality(*, flags: list[str] | None = None, frames: int = 60) -> PoseQuality:
    """A clean PoseQuality, optionally carrying Stage 7 flags."""
    return PoseQuality(
        frames_with_pose=frames,
        frames_missing=0,
        detection_rate=1.0,
        mean_visibility=0.9,
        longest_gap_frames=0,
        interpolated_frames=0,
        torso_scale_px=120.0,
        estimated_camera_view=CameraView.SIDE_ON,
        usable=True,
        flags=list(flags or []),
    )


def make_pose_sequence(
    *,
    frame_count: int = 60,
    fps: float = 30.0,
    width_px: int = 360,
    height_px: int = 640,
    handedness: Handedness = Handedness.RIGHT,
    torso_px_fraction: float = 0.25,
    visibility: float = 0.9,
    detected: np.ndarray | None = None,
) -> PoseSequence:
    """A seam object in raw normalized image coordinates (y DOWN, 0..1).

    The racket wrist sweeps across the frame; the off hand barely moves. Units
    are what MediaPipe produces, so Stage 7 has real work to do.
    """
    landmarks = np.zeros((frame_count, NUM_LANDMARKS, 4), dtype=np.float32)
    hip_y = 0.6
    shoulder_y = hip_y - torso_px_fraction
    frames = np.arange(frame_count, dtype=np.float64)
    sweep = 0.2 + 0.6 * frames / max(frame_count - 1, 1)

    racket = RIGHT_WRIST if handedness != Handedness.LEFT else LEFT_WRIST
    off_hand = LEFT_WRIST if racket == RIGHT_WRIST else RIGHT_WRIST

    landmarks[:, LEFT_HIP, :2] = (0.45, hip_y)
    landmarks[:, RIGHT_HIP, :2] = (0.55, hip_y)
    landmarks[:, LEFT_SHOULDER, :2] = (0.44, shoulder_y)
    landmarks[:, RIGHT_SHOULDER, :2] = (0.56, shoulder_y)
    landmarks[:, racket, 0] = sweep
    landmarks[:, racket, 1] = shoulder_y
    landmarks[:, off_hand, 0] = 0.48
    landmarks[:, off_hand, 1] = hip_y - 0.05
    landmarks[:, LEFT_ELBOW, :2] = (0.42, shoulder_y + 0.05)
    landmarks[:, RIGHT_ELBOW, :2] = (0.58, shoulder_y + 0.05)
    landmarks[:, :, 3] = visibility

    return PoseSequence(
        landmarks=landmarks,
        world=np.zeros((frame_count, NUM_LANDMARKS, 3), dtype=np.float32),
        timestamps_s=np.arange(frame_count, dtype=np.float64) / float(fps),
        detected=(
            np.ones((frame_count,), dtype=bool)
            if detected is None
            else np.asarray(detected, dtype=bool)
        ),
        width_px=width_px,
        height_px=height_px,
    )


def make_pose_sequence_with_valid_run(
    *,
    clip_len_s: float,
    valid_run_start_s: float,
    valid_run_len_s: float,
    fps: float = 30.0,
    swing_centre_s: float | None = None,
    swing_width_s: float = 0.25,
    handedness: Handedness = Handedness.RIGHT,
    torso_px_fraction: float = 0.25,
    visibility: float = 0.9,
) -> PoseSequence:
    """A clip shaped like REAL footage: valid in the middle, absent at both ends.

    PIPELINE.md 7.1.4: the end-to-end-valid fixtures above cannot express the
    shape of the failure Stage 7's whole-window gap gate was rejecting, which is
    why 670 green unit tests said nothing about a defect that rejected a third
    of the corpus. Outside ``[valid_run_start_s, valid_run_start_s +
    valid_run_len_s)`` the frames are undetected and their landmarks are zeroed,
    exactly as MediaPipe leaves a frame it found nobody in -- the player is
    small in frame or turned away before the swing and has walked off after it.

    The racket wrist reaches away from the hips in a bump centred on
    ``swing_centre_s`` (the middle of the valid run by default), so the Tier 2
    anchor has something to find and the test can say where it should land.
    """
    frame_count = max(int(round(clip_len_s * fps)), 0)
    base = make_pose_sequence(
        frame_count=frame_count,
        fps=fps,
        handedness=handedness,
        torso_px_fraction=torso_px_fraction,
        visibility=visibility,
    )
    landmarks = np.array(base.landmarks, copy=True)
    timestamps_s = np.asarray(base.timestamps_s, dtype=np.float64)

    centre = (
        valid_run_start_s + valid_run_len_s / 2.0
        if swing_centre_s is None
        else float(swing_centre_s)
    )
    racket = RIGHT_WRIST if handedness != Handedness.LEFT else LEFT_WRIST
    reach = 0.35 * np.exp(-(((timestamps_s - centre) / swing_width_s) ** 2))
    landmarks[:, racket, 0] = 0.5 + reach
    landmarks[:, racket, 1] = 0.6 - torso_px_fraction - reach

    detected = (timestamps_s >= valid_run_start_s) & (
        timestamps_s < valid_run_start_s + valid_run_len_s
    )
    landmarks[~detected] = 0.0
    return dataclasses.replace(base, landmarks=landmarks, detected=detected)


def punch_hole(seq: PoseSequence, start: int, end: int) -> PoseSequence:
    """Blank frames ``[start, end)`` the way a lost detection blanks them."""
    landmarks = np.array(seq.landmarks, copy=True)
    detected = np.array(seq.detected, copy=True)
    landmarks[start:end] = 0.0
    detected[start:end] = False
    return dataclasses.replace(seq, landmarks=landmarks, detected=detected)
