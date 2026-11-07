"""Swing direction re-measured on the racket wrist after Stage 8 (synthetic only).

Stage 7's ``swing_direction_sign`` is computed before handedness exists: the
longer-path wrist's GLOBAL speed peak against the slowest frame anywhere
before it. ``contact.resolve_swing_direction`` re-measures it as the sign of
the racket wrist's x-displacement over the short window leading INTO its own
speed peak. Each trajectory below is built in closed form, so the right answer is
known by construction.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.contact import (
    SWING_DIRECTION_LEAD_FRAMES,
    SWING_DIRECTION_MIN_DISPLACEMENT_TU,
    local_swing_direction_sign,
    resolve_swing_direction,
)
from app.analysis.normalize import (
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    swing_direction_sign,
)
from app.analysis.smoothing import central_difference
from app.models.enums import Handedness, HandednessSource
from app.models.internal import NormalizedSequence
from app.models.responses import HandednessResult

FPS = 30.0
NUM_LANDMARKS = 33


def hand(handedness: Handedness) -> HandednessResult:
    return HandednessResult(
        handedness=handedness, confidence=0.9, source=HandednessSource.USER_HINT
    )


def ease(start: float, end: float, frames: int) -> np.ndarray:
    """Cosine ease from ``start`` to ``end``: zero speed at both ends, peak mid-way."""
    phase = np.linspace(0.0, np.pi, frames)
    return start + (end - start) * (1.0 - np.cos(phase)) / 2.0


def hold(value: float, frames: int) -> np.ndarray:
    return np.full(frames, value, dtype=np.float64)


def sequence(
    racket_x: np.ndarray,
    *,
    racket: int = RIGHT_WRIST,
    stage7_sign: int | None = None,
) -> NormalizedSequence:
    """A body-frame sequence whose racket wrist follows ``racket_x`` at y = 0.8.

    Velocity is the central difference of the positions, exactly as Stage 7
    derives it. The other wrist is held still. ``stage7_sign`` defaults to what
    Stage 7 itself computes for this trajectory.
    """
    x = np.asarray(racket_x, dtype=np.float64)
    count = int(x.shape[0])
    timestamps = np.arange(count, dtype=np.float64) / FPS
    points = np.zeros((count, NUM_LANDMARKS, 2), dtype=np.float64)
    points[:, LEFT_HIP] = (-0.15, 0.0)
    points[:, RIGHT_HIP] = (0.15, 0.0)
    points[:, LEFT_SHOULDER] = (-0.2, 1.0)
    points[:, RIGHT_SHOULDER] = (0.2, 1.0)
    off = LEFT_WRIST if racket == RIGHT_WRIST else RIGHT_WRIST
    points[:, off] = (-0.1, 0.5)
    points[:, racket, 0] = x
    points[:, racket, 1] = 0.8
    velocity = central_difference(points, timestamps)
    sign = (
        swing_direction_sign(points[:, racket, :], timestamps)
        if stage7_sign is None
        else stage7_sign
    )
    return NormalizedSequence(
        points=points,
        velocity=velocity,
        acceleration=central_difference(velocity, timestamps),
        timestamps_s=timestamps,
        valid=np.ones((count,), dtype=bool),
        visibility=np.full((count, NUM_LANDMARKS), 0.9, dtype=np.float64),
        swing_direction_sign=int(sign),
        torso_scale_px=120.0,
        width_px=360,
        height_px=640,
        origin_px=np.tile(np.array([180.0, 320.0]), (count, 1)),
    )


def test_the_window_constants_are_the_documented_ones() -> None:
    assert SWING_DIRECTION_LEAD_FRAMES == 5
    assert SWING_DIRECTION_MIN_DISPLACEMENT_TU == pytest.approx(0.10)


def test_two_swings_of_opposite_direction_measure_the_faster_one() -> None:
    """A slow leftward swing, a pause, then a FAST rightward one.

    Stage 7 compares the fast peak with the slowest frame anywhere before it --
    the very first idle frame, out at x = +1.0 -- and so reads -1. Around the
    fast swing's own peak the hand plainly travels +x.
    """
    x = np.concatenate([
        hold(1.0, 10),
        ease(1.0, -1.0, 40),  # slow swing, leftward
        hold(-1.0, 10),
        ease(-1.0, 0.6, 12),  # fast swing, rightward: the global speed peak
        hold(0.6, 10),
    ])
    seq = sequence(x)
    assert seq.swing_direction_sign == -1  # Stage 7's reading: the defect
    assert local_swing_direction_sign(seq, Handedness.RIGHT) == 1
    resolved = resolve_swing_direction(seq, hand(Handedness.RIGHT))
    assert resolved.swing_direction_sign == 1
    # Only the sign is replaced; the arrays are the same objects.
    assert resolved.points is seq.points
    assert resolved.velocity is seq.velocity


def test_the_mirror_image_resolves_to_minus_one() -> None:
    x = -np.concatenate([
        hold(1.0, 10), ease(1.0, -1.0, 40), hold(-1.0, 10), ease(-1.0, 0.6, 12), hold(0.6, 10),
    ])
    seq = sequence(x)
    assert seq.swing_direction_sign == 1
    assert resolve_swing_direction(seq, hand(Handedness.RIGHT)).swing_direction_sign == -1


def test_a_long_idle_gap_before_the_swing_does_not_set_the_reference() -> None:
    """100 idle frames at x = +0.8, a takeback to -0.6, then the drive to +1.2.

    Stage 7's reference is frame 0 (zero speed, the first minimum), which sits
    to the right of the drive's peak, so it reads -1; the drive goes +x.
    """
    x = np.concatenate([
        hold(0.8, 100),
        ease(0.8, -0.6, 25),  # takeback, slower
        ease(-0.6, 1.2, 10),  # drive, the speed peak
        hold(1.2, 10),
    ])
    seq = sequence(x)
    assert seq.swing_direction_sign == -1
    resolved = resolve_swing_direction(seq, hand(Handedness.RIGHT))
    assert resolved.swing_direction_sign == 1


def test_a_peak_after_contact_is_read_from_the_drive_not_the_follow_through() -> None:
    """The serve shape: the speed peak comes AFTER the strike.

    The hand drives +x into a peak, then sweeps back further and almost as fast
    across the body. A window centred on the peak is dominated by that return
    (-x); the window leading into the peak reads the drive (+x).
    """
    drive = -0.3 + 1.3 * np.linspace(0.0, 1.0, 5)[1:] ** 2  # accelerating to the strike
    follow = 1.0 - 3.0 * np.sin(np.linspace(0.0, np.pi / 2.0, 11)[1:])  # sweep back to -2.0
    x = np.concatenate([hold(-0.3, 10), drive, follow, hold(-2.0, 10)])
    seq = sequence(x, stage7_sign=-1)
    speed = np.linalg.norm(seq.velocity[:, RIGHT_WRIST], axis=1)
    peak = int(np.argmax(speed))
    assert 10 <= peak <= 13  # the peak is in the drive...
    centred = x[peak + 5] - x[peak - 5]
    assert centred < 0.0  # ...yet a window centred on it would read -x
    assert local_swing_direction_sign(seq, Handedness.RIGHT) == 1
    assert resolve_swing_direction(seq, hand(Handedness.RIGHT)).swing_direction_sign == 1


def test_a_single_clean_swing_is_unchanged() -> None:
    x = np.concatenate([hold(0.2, 8), ease(0.2, -0.5, 10), ease(-0.5, 1.5, 12), hold(1.5, 8)])
    seq = sequence(x)
    assert seq.swing_direction_sign == 1
    assert local_swing_direction_sign(seq, Handedness.RIGHT) == 1
    assert resolve_swing_direction(seq, hand(Handedness.RIGHT)) is seq


def test_too_little_motion_falls_back_to_the_stage_7_value() -> None:
    small = SWING_DIRECTION_MIN_DISPLACEMENT_TU / 4.0
    x = np.concatenate([hold(0.0, 10), ease(0.0, small, 10), hold(small, 10)])
    for stage7 in (1, -1):
        seq = sequence(x, stage7_sign=stage7)
        assert local_swing_direction_sign(seq, Handedness.RIGHT) is None
        assert resolve_swing_direction(seq, hand(Handedness.RIGHT)) is seq
        assert resolve_swing_direction(seq, hand(Handedness.RIGHT)).swing_direction_sign == stage7


def test_an_unseen_racket_wrist_falls_back_to_the_stage_7_value() -> None:
    x = np.concatenate([hold(0.8, 20), ease(0.8, -0.6, 10), hold(-0.6, 10)])
    seq = sequence(x, stage7_sign=1)
    seq.visibility[:, RIGHT_WRIST] = 0.05
    assert local_swing_direction_sign(seq, Handedness.RIGHT) is None
    assert resolve_swing_direction(seq, hand(Handedness.RIGHT)).swing_direction_sign == 1


def test_unknown_handedness_falls_back_to_the_stage_7_value() -> None:
    x = np.concatenate([hold(0.8, 20), ease(0.8, -0.6, 10), hold(-0.6, 10)])
    seq = sequence(x, stage7_sign=1)
    assert local_swing_direction_sign(seq, Handedness.UNKNOWN) is None
    assert resolve_swing_direction(seq, hand(Handedness.UNKNOWN)) is seq


def test_the_racket_wrist_is_the_one_measured() -> None:
    """A left-handed swing: the LEFT wrist is measured, the still right one ignored."""
    x = np.concatenate([hold(0.8, 20), ease(0.8, -0.6, 10), hold(-0.6, 10)])
    seq = sequence(x, racket=LEFT_WRIST, stage7_sign=1)
    assert local_swing_direction_sign(seq, Handedness.LEFT) == -1
    assert local_swing_direction_sign(seq, Handedness.RIGHT) is None  # still wrist
