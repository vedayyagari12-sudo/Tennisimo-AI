"""Synthetic long sessions for the segmentation tests (SESSION_MODE.md 4.5).

Closed-form, so where every swing is is known by construction. Positions are
written in body-frame TORSO UNITS and converted to raw MediaPipe image
coordinates (0..1, y down) for a landscape camera, so Stage 7 has real work.

One swing is a sum of three logistic steps of the racket wrist, centred on the
contact time ``c``:

* takeback   0.7 TU backward, centred c - 0.35 s, width 0.08 s (~2.2 TU/s)
* drive      2.0 TU forward and 0.9 TU up, centred c, width 0.05 s (~11 TU/s)
* recovery   1.3 TU back and 0.9 TU down, centred c + 0.5 s, width 0.15 s

Steps are additive, so swings may sit arbitrarily close together, and the
wrist returns to its ready position after every swing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

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
from app.models.enums import Handedness
from app.models.internal import PoseSequence

NUM_LANDMARKS = 33
FPS = 30.0
WIDTH_PX = 1280
HEIGHT_PX = 720
READY_X_TU = 0.2
READY_Y_TU = 0.5


@dataclass(frozen=True)
class Swing:
    contact_s: float
    direction: int = 1        # +1 forehand-like, -1 mirrored
    scale: float = 1.0        # multiplies every displacement (and speed)
    stutter_s: float = 0.0    # > 0 splits the drive into two pushes this far apart


@dataclass
class Session:
    duration_s: float
    swings: Sequence[Swing] = ()
    handedness: Handedness = Handedness.RIGHT
    torso_fraction: float = 0.15                  # torso length / image height
    torso_fraction_end: float | None = None       # linear change over the session
    walk_speed_tu_s: float = 0.0                  # whole-body drift along x
    arm_swing_tu: float = 0.0                     # walking arm pendulum amplitude
    bounce_tu: float = 0.0                        # racket wrist vertical bounce
    holes: Sequence[tuple[int, int]] = ()         # undetected frame ranges
    identity_jump_s: float | None = None          # body shifts 1.5 TU here
    occlusion_frames: Sequence[int] = ()          # racket wrist spikes, unseen
    start_s: float = 0.0
    extra: dict[str, float] = field(default_factory=dict)


def _step(t: np.ndarray, centre: float, width: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(t - centre) / width))


def racket_path_tu(times: np.ndarray, swings: Sequence[Swing]) -> np.ndarray:
    """(T, 2) racket wrist in body-frame TU, y up."""
    x = np.full(times.shape, READY_X_TU, dtype=np.float64)
    y = np.full(times.shape, READY_Y_TU, dtype=np.float64)
    for swing in swings:
        c, d, k = swing.contact_s, float(swing.direction), swing.scale
        if swing.stutter_s > 0.0:
            # One swing whose forward drive hesitates: two pushes, one swing.
            drive = 1.2 * _step(times, c, 0.05) + 0.8 * _step(
                times, c + swing.stutter_s, 0.05
            )
        else:
            drive = 2.0 * _step(times, c, 0.05)
        x += d * k * (
            -0.7 * _step(times, c - 0.35, 0.08)
            + drive
            - 1.3 * _step(times, c + 0.5, 0.15)
        )
        y += k * (0.45 * drive - 0.9 * _step(times, c + 0.5, 0.15))
    return np.stack([x, y], axis=1)


def build_session(session: Session) -> PoseSequence:
    count = int(round(session.duration_s * FPS))
    times = session.start_s + np.arange(count, dtype=np.float64) / FPS
    local = times - session.start_s
    end_fraction = (
        session.torso_fraction if session.torso_fraction_end is None else session.torso_fraction_end
    )
    torso = session.torso_fraction + (end_fraction - session.torso_fraction) * (
        local / max(session.duration_s, 1e-9)
    )

    # Body-frame TU positions (y up), origin at mid-hip.
    body = np.zeros((count, NUM_LANDMARKS, 2), dtype=np.float64)
    body[:, LEFT_HIP] = (-0.2, 0.0)
    body[:, RIGHT_HIP] = (0.2, 0.0)
    body[:, LEFT_SHOULDER] = (-0.25, 1.0)
    body[:, RIGHT_SHOULDER] = (0.25, 1.0)

    racket = RIGHT_WRIST if session.handedness is Handedness.RIGHT else LEFT_WRIST
    off_hand = LEFT_WRIST if racket == RIGHT_WRIST else RIGHT_WRIST
    racket_elbow = RIGHT_ELBOW if racket == RIGHT_WRIST else LEFT_ELBOW
    off_elbow = LEFT_ELBOW if racket == RIGHT_WRIST else RIGHT_ELBOW
    racket_shoulder = RIGHT_SHOULDER if racket == RIGHT_WRIST else LEFT_SHOULDER

    wrist = racket_path_tu(local, session.swings)
    pendulum = session.arm_swing_tu * np.sin(2.0 * np.pi * 1.0 * local)
    wrist[:, 0] += pendulum
    wrist[:, 1] += session.bounce_tu * np.sin(2.0 * np.pi * 2.0 * local)
    body[:, racket] = wrist
    body[:, racket_elbow] = (body[:, racket_shoulder] + wrist) / 2.0
    body[:, off_hand, 0] = -0.3 - pendulum
    body[:, off_hand, 1] = 0.3
    body[:, off_elbow] = (-0.3, 0.65)

    # Whole-body translation (walking) and an identity jump.
    shift = np.zeros((count, 2), dtype=np.float64)
    shift[:, 0] = session.walk_speed_tu_s * local
    if session.identity_jump_s is not None:
        shift[local >= session.identity_jump_s, 0] += 1.5

    landmarks = np.zeros((count, NUM_LANDMARKS, 4), dtype=np.float32)
    hip_x, hip_y = 0.5, 0.65
    torso_x = torso * HEIGHT_PX / WIDTH_PX   # one TU of x in raw x units
    positions = body + shift[:, None, :]
    landmarks[:, :, 0] = hip_x + positions[:, :, 0] * torso_x[:, None]
    landmarks[:, :, 1] = hip_y - positions[:, :, 1] * torso[:, None]
    landmarks[:, :, 3] = 0.9

    for frame in session.occlusion_frames:
        landmarks[frame, racket, 0] += 1.0 * torso_x[frame]
        landmarks[frame, racket, 3] = 0.1

    detected = np.ones((count,), dtype=bool)
    for start, end in session.holes:
        landmarks[start:end] = 0.0
        detected[start:end] = False

    return PoseSequence(
        landmarks=landmarks,
        world=np.zeros((count, NUM_LANDMARKS, 3), dtype=np.float32),
        timestamps_s=times,
        detected=detected,
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
    )


def evenly_spaced(count: int, *, first_s: float = 2.0, spacing_s: float = 3.0) -> list[Swing]:
    return [Swing(contact_s=first_s + spacing_s * i) for i in range(count)]
