"""Internal value objects that are not part of the HTTP contract (PIPELINE.md §3).

PURE: plain numpy plus scalars. No MediaPipe objects, no file handles, no
iterators. ``mediapipe`` must never be importable from this module -- the seam
has to be constructible and testable with MediaPipe absent entirely
(PIPELINE.md §4.4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RawPoseSequence:
    """Stage 6 output, before the seam is assembled (PIPELINE.md Stage 6.9).

    landmarks:    (T, 33, 4) float32 -- x, y, z, visibility (NOT presence)
    world:        (T, 33, 3) float32 -- metres relative to the hip midpoint
    timestamps_s: (T,) float64 -- Stage 5's actual PTS, full precision
    detected:     (T,) bool
    """

    landmarks: np.ndarray
    world: np.ndarray
    timestamps_s: np.ndarray
    detected: np.ndarray


@dataclass(frozen=True)
class PoseSequence:
    """THE FIRST SEAM (PIPELINE.md §4.1). Impure collection ends here."""

    landmarks: np.ndarray      # (T, 33, 4) float32
    world: np.ndarray          # (T, 33, 3) float32
    timestamps_s: np.ndarray   # (T,) float64
    detected: np.ndarray       # (T,) bool
    width_px: int
    height_px: int


@dataclass(frozen=True)
class NormalizedSequence:
    """Stage 7 output (PIPELINE.md Stage 7). Body-frame, torso-unit kinematics.

    points:       (T, 33, 2) float64 -- smoothed x, y in torso units, mid-hip
                  origin, y positive up, aspect-corrected.
    velocity:     (T, 33, 2) float64 -- TU/s, central difference on actual dt.
    acceleration: (T, 33, 2) float64 -- TU/s^2, central difference on velocity.
    timestamps_s: (T,) float64 -- full-precision PTS carried from the seam.
    valid:        (T,) bool -- frame passed the visibility gate (interpolated
                  frames stay False so downstream code can tell them apart).
    visibility:   (T, 33) float64 -- channel 4 of the seam, unmodified.
    swing_direction_sign: -1 or +1 (Stage 7 step 9).
    torso_scale_px: median shoulder-to-hip length in the aspect-corrected
                  normalized frame; one TU.
    origin_px:    (T, 2) float64 -- the per-frame mid-hip in POSE-STREAM PIXELS
                  with y pointing DOWN (the raw image convention), captured
                  BEFORE step 5 translated it to the origin. ``points`` is
                  body-frame, so the mid-hip is identically (0, 0) there and the
                  subject's absolute position in the image is otherwise
                  unrecoverable. Stage 10 needs it: ``exclusion_boxes`` and
                  ``seed_xy`` are CAL_SPACE pixel quantities and cannot be built
                  without an origin.

                  A landmark returns to pose-stream pixels as
                  ``origin_px[t] + points[t, k] * torso_scale_px * [1, -1]``
                  (the ``[1, -1]`` undoes the y-flip).

                  CAVEAT, and it is why this field does NOT un-``None``
                  ``weight_transfer_tu`` / ``balance_sway_tu``: this is an IMAGE
                  coordinate, so it moves when the CAMERA moves. On a handheld
                  phone a pan is indistinguishable from a weight transfer. It
                  answers "where is the player in the frame" correctly under any
                  camera motion -- which is all the ball seam asks -- and does
                  not answer "how far did the player move".
    """

    points: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    timestamps_s: np.ndarray
    valid: np.ndarray
    visibility: np.ndarray
    swing_direction_sign: int
    torso_scale_px: float
    width_px: int
    height_px: int
    origin_px: np.ndarray
