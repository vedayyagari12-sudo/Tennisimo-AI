"""Player tracking for a fixed camera: who hit the ball, where they stood, and
what the stroke was (2026-11-22). PURE: arrays in, numbers out. No file, no
clock, no model.

The court engine already finds the ball's hits, bounces and rallies. What it
lacked in match mode was the PLAYER at each hit (``PoseHit``): which end hit
it, where the feet were (so a shot can be drawn from the hitter to the
landing), and forehand or backhand. This module turns pose landmarks around a
ball hit into that evidence.

How the player is found and followed
------------------------------------
* **Identity comes from the court, not from appearance.** The camera is
  fixed and the court is calibrated, so each player is the person standing in
  their own half. ``services/player_tracking.py`` runs pose on a crop of each
  half, and drops any detection whose feet are not on that side of the net
  (a spectator, a player on the next court). No re-identification is needed
  because the two players never swap halves between hits.
* **The hitter is the player whose wrist was at the ball.** At a ball hit the
  hitting wrist and the ball are in the same place. ``contact_candidate`` takes
  the wrist trajectory around the hit, requires a real swing (wrist speed in
  torso lengths per second), and requires the wrist to be within the same
  1.2 m gate the engine uses of the ball's image position at the hit time.
  A player standing still near the ball does not qualify.
* **Handedness is learned per player** from which wrist swings fastest across
  their hits (``estimate_handedness``); with too few swings it stays unknown.
* **Stroke type** (``classify_stroke``) is rule-based from one frame: a wrist
  above the head is a serve (at a flight's first hit) or an overhead; near the
  net it is a volley; otherwise the contact wrist's side of the body, in the
  player's OWN left/right frame, against their handedness, is forehand or
  backhand. When the body is turned too side-on to tell, or handedness is
  unknown, the type is None, never a guess.

Landmarks are MediaPipe BlazePose (33, 4): x, y as fractions of the FULL
frame (the crop has been undone), z, visibility. Pixels below are TRACK_SPACE.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

from app.analysis.ball_events import POSE_HIT_WRIST_GATE_M, PoseHit
from app.models.court import CourtEnd

F32Array = npt.NDArray[np.float32]

# BlazePose landmark indices.
NOSE: Final[int] = 0
L_SHOULDER: Final[int] = 11
R_SHOULDER: Final[int] = 12
L_WRIST: Final[int] = 15
R_WRIST: Final[int] = 16
L_HIP: Final[int] = 23
R_HIP: Final[int] = 24
L_ANKLE: Final[int] = 27
R_ANKLE: Final[int] = 28

PLAYER_TRACK_VERSION: Final[str] = "player_track_v1"

#: A landmark counts only at least this visible.
MIN_VISIBILITY: Final[float] = 0.3
#: Frames this close to the ball hit (seconds, each side) judge the swing.
CONTACT_WINDOW_S: Final[float] = 0.20
#: A frame may stand in for the hit time when this close to it.
CONTACT_NEAREST_S: Final[float] = 0.08
#: The hitting wrist must move at least this fast: torso lengths per second.
MIN_SWING_TORSOS_PER_S: Final[float] = 2.0
#: Shoulders narrower than this share of the torso mean the body is side-on
#: and left/right cannot be read.
MIN_SHOULDER_RATIO: Final[float] = 0.20
#: Contact wrist this far (torso lengths) to a side of the hips counts as that side.
SIDE_MARGIN_TORSOS: Final[float] = 0.10
#: A wrist this far (torso lengths) above the nose is overhead.
OVERHEAD_MARGIN_TORSOS: Final[float] = 0.05
#: A hitter within this many metres of the net is volleying.
VOLLEY_MAX_ALONG_M: Final[float] = 5.0
#: Handedness needs this many swings, and this share of them by one wrist.
HANDEDNESS_MIN_SWINGS: Final[int] = 3
HANDEDNESS_MIN_SHARE: Final[float] = 0.6

Handedness = Literal["right", "left"]


@dataclass(frozen=True, eq=False)
class PlayerFrame:
    """One detected pose: its time and its landmarks in the FULL frame."""

    time_s: float
    landmarks: F32Array


@dataclass(frozen=True, eq=False)
class Contact:
    """The swing that met the ball at a hit."""

    wrist: int
    wrist_xy_px: tuple[float, float]
    #: Wrist speed at its peak around the hit, torso lengths per second.
    peak_speed: float
    #: Wrist to ball, pixels, at the hit time.
    distance_px: float
    #: The pose frame nearest the hit, for the stroke rules and the feet.
    landmarks: F32Array
    frame_time_s: float
    frame_size: tuple[int, int]


def _visible(lm: F32Array, index: int) -> bool:
    return bool(lm[index, 3] >= MIN_VISIBILITY)


def _point(lm: F32Array, index: int, size: tuple[int, int]) -> npt.NDArray[np.float64]:
    return np.array([float(lm[index, 0]) * size[0], float(lm[index, 1]) * size[1]], dtype=np.float64)


def torso_px(lm: F32Array, size: tuple[int, int]) -> float | None:
    """Mid-shoulders to mid-hips, pixels. The body's own ruler: a far player
    is small, so speeds and offsets are judged in torso lengths. None when a
    shoulder or hip is not visible."""
    needed = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)
    if not all(_visible(lm, i) for i in needed):
        return None
    shoulders = (_point(lm, L_SHOULDER, size) + _point(lm, R_SHOULDER, size)) / 2.0
    hips = (_point(lm, L_HIP, size) + _point(lm, R_HIP, size)) / 2.0
    length = float(np.linalg.norm(shoulders - hips))
    return length if length > 1e-6 else None


def feet_point(lm: F32Array, size: tuple[int, int]) -> tuple[float, float] | None:
    """Midpoint of the visible ankles, pixels: the one pose point the court
    homography may map to metres (feet are on the ground). None when neither
    ankle is visible."""
    ankles = [_point(lm, i, size) for i in (L_ANKLE, R_ANKLE) if _visible(lm, i)]
    if not ankles:
        return None
    mean = np.mean(ankles, axis=0)
    return (float(mean[0]), float(mean[1]))


def _wrist_track(
    frames: Sequence[PlayerFrame], wrist: int, size: tuple[int, int]
) -> list[tuple[float, npt.NDArray[np.float64]]]:
    return [
        (f.time_s, _point(f.landmarks, wrist, size)) for f in frames if _visible(f.landmarks, wrist)
    ]


def _position_at(
    track: Sequence[tuple[float, npt.NDArray[np.float64]]], time_s: float
) -> npt.NDArray[np.float64] | None:
    """Linear interpolation between the two samples around ``time_s``; the
    nearest sample when outside, if within ``CONTACT_NEAREST_S``."""
    for (t0, p0), (t1, p1) in zip(track, track[1:], strict=False):
        if t0 <= time_s <= t1:
            span = t1 - t0
            return p0 if span <= 0.0 else p0 + (p1 - p0) * ((time_s - t0) / span)
    nearest = min(track, key=lambda item: abs(item[0] - time_s))
    return nearest[1] if abs(nearest[0] - time_s) <= CONTACT_NEAREST_S else None


def contact_candidate(
    frames: Sequence[PlayerFrame],
    event_time_s: float,
    ball_xy_px: tuple[float, float],
    frame_size: tuple[int, int],
    scale_px_per_m: float,
) -> Contact | None:
    """The swing of one player at a ball hit, or None.

    A wrist qualifies when (1) it moved like a swing in the window around the
    hit and (2) at the hit time it is within the engine's wrist gate of the
    ball. The nearer qualifying wrist wins (a two-handed backhand has two).
    """
    window = sorted(
        (f for f in frames if abs(f.time_s - event_time_s) <= CONTACT_WINDOW_S),
        key=lambda f: f.time_s,
    )
    if len(window) < 2:
        return None
    torsos = [t for t in (torso_px(f.landmarks, frame_size) for f in window) if t is not None]
    if not torsos:
        return None
    torso = statistics.median(torsos)
    gate = POSE_HIT_WRIST_GATE_M * scale_px_per_m
    ball = np.asarray(ball_xy_px, dtype=np.float64)

    best: tuple[float, Contact] | None = None
    nearest = min(window, key=lambda f: abs(f.time_s - event_time_s))
    for wrist in (L_WRIST, R_WRIST):
        track = _wrist_track(window, wrist, frame_size)
        if len(track) < 2:
            continue
        peak = 0.0
        for (t0, p0), (t1, p1) in zip(track, track[1:], strict=False):
            if t1 > t0:
                peak = max(peak, float(np.linalg.norm(p1 - p0)) / (t1 - t0) / torso)
        if peak < MIN_SWING_TORSOS_PER_S:
            continue
        position = _position_at(track, event_time_s)
        if position is None:
            continue
        distance = float(np.linalg.norm(position - ball))
        if distance > gate:
            continue
        contact = Contact(
            wrist=wrist,
            wrist_xy_px=(float(position[0]), float(position[1])),
            peak_speed=peak,
            distance_px=distance,
            landmarks=nearest.landmarks,
            frame_time_s=nearest.time_s,
            frame_size=frame_size,
        )
        if best is None or distance < best[0]:
            best = (distance, contact)
    return None if best is None else best[1]


def lateral_offset(contact: Contact) -> float | None:
    """The contact wrist's side of the body, in torso lengths, in the PLAYER's
    own frame: positive is the player's right, negative their left. Uses the
    anatomical left/right of the landmarks, so it holds facing the camera or
    away from it. None when the body is too side-on to tell."""
    lm, size = contact.landmarks, contact.frame_size
    torso = torso_px(lm, size)
    if torso is None:
        return None
    right_axis = _point(lm, R_SHOULDER, size) - _point(lm, L_SHOULDER, size)
    width = float(np.linalg.norm(right_axis))
    if width < MIN_SHOULDER_RATIO * torso:
        return None
    hips = (_point(lm, L_HIP, size) + _point(lm, R_HIP, size)) / 2.0
    wrist = np.asarray(contact.wrist_xy_px, dtype=np.float64)
    return float(np.dot(wrist - hips, right_axis / width)) / torso


def estimate_handedness(fastest_wrists: Sequence[int]) -> Handedness | None:
    """The dominant hand from which wrist swung at each of a player's hits.
    None below ``HANDEDNESS_MIN_SWINGS`` swings or without a clear majority."""
    right = sum(1 for w in fastest_wrists if w == R_WRIST)
    left = sum(1 for w in fastest_wrists if w == L_WRIST)
    total = right + left
    if total < HANDEDNESS_MIN_SWINGS:
        return None
    if right / total >= HANDEDNESS_MIN_SHARE:
        return "right"
    if left / total >= HANDEDNESS_MIN_SHARE:
        return "left"
    return None


def classify_stroke(
    contact: Contact,
    handedness: Handedness | None,
    *,
    launch: bool,
    ground_along_m: float | None,
) -> str | None:
    """Serve, overhead, volley, forehand or backhand; None when unclear.

    ``launch`` is True for the first hit of a flight (the ball appears at the
    racket), ``ground_along_m`` the hitter's distance from the net along the
    court (None when their feet could not be placed).
    """
    lm, size = contact.landmarks, contact.frame_size
    torso = torso_px(lm, size)
    if torso is None:
        return None
    if _visible(lm, NOSE):
        nose_y = float(_point(lm, NOSE, size)[1])
        if contact.wrist_xy_px[1] < nose_y - OVERHEAD_MARGIN_TORSOS * torso:
            return "serve" if launch else "overhead"
    if ground_along_m is not None and abs(ground_along_m) <= VOLLEY_MAX_ALONG_M:
        return "volley"
    if handedness is None:
        return None
    offset = lateral_offset(contact)
    if offset is None:
        return None
    side = offset if handedness == "right" else -offset
    if side >= SIDE_MARGIN_TORSOS:
        return "forehand"
    if side <= -SIDE_MARGIN_TORSOS:
        return "backhand"
    return None


def pose_hit_from_contact(
    event_time_s: float,
    contact: Contact,
    end: CourtEnd,
    shot_type: str | None,
    gate_px: float,
) -> PoseHit:
    """The evidence the court engine takes. Its time is the BALL hit's time:
    the engine matches by time and then verifies the wrist against the ball,
    which ``contact_candidate`` has already done."""
    closeness = 1.0 - min(contact.distance_px / gate_px, 1.0) if gate_px > 0.0 else 0.0
    return PoseHit(
        time_s=event_time_s,
        wrist_xy_px=contact.wrist_xy_px,
        end=end,
        confidence=float(max(0.3, closeness)),
        shot_type=shot_type,
        feet_xy_px=feet_point(contact.landmarks, contact.frame_size),
    )


def on_players_side(end: CourtEnd, ground_xy_m: tuple[float, float] | None, slack_m: float = 1.0) -> bool:
    """A detection is this end's player only when the feet are on that side of
    the net (court y > 0 is the far half), with ``slack_m`` for a step over
    the line. No feet means no evidence against, so it is allowed."""
    if ground_xy_m is None:
        return True
    along = ground_xy_m[1]
    return along >= -slack_m if end is CourtEnd.FAR else along <= slack_m


__all__ = [
    "CONTACT_WINDOW_S",
    "Contact",
    "Handedness",
    "PLAYER_TRACK_VERSION",
    "PlayerFrame",
    "classify_stroke",
    "contact_candidate",
    "estimate_handedness",
    "feet_point",
    "lateral_offset",
    "on_players_side",
    "pose_hit_from_contact",
    "torso_px",
]

