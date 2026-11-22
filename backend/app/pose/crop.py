"""Zoom-in retry for a small or unclear player (2026-11-09). PURE: arrays in,
numbers out.

Filming yourself usually means a phone on a fence or a bag, several metres
away: the player is a small part of the frame. MediaPipe's person detector
sees the whole frame shrunk to a few hundred pixels, so a small player is
missed (``no_pose_detected``) or tracked with low visibility
(``pose_quality_too_low``). Production, 2026-10-24 to 10-30: 25 of 65 jobs
failed on those two codes.

The retry crops the full-resolution frame to a box around the player and runs
pose on the crop, so the player fills far more of the detector's input. The
landmarks are mapped back to full-frame coordinates (``uncrop_landmarks``), so
nothing downstream knows a crop happened.

Where the player is comes from the first pass's own detections when there are
any (``landmark_box``), else from where the picture moves (``motion_box``).
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

#: ``(x0, y0, x1, y1)`` as fractions of the (rotated) frame.
CropBox = tuple[float, float, float, float]

#: Landmarks at least this visible place the player.
BOX_MIN_VISIBILITY: Final[float] = 0.3
#: The box grows by this share of its own size on every side: the swing
#: reaches past the body, and the player moves between frames.
BOX_MARGIN: Final[float] = 0.5
#: A box this short (fraction of frame height) is grown to it: a crop
#: tighter than this upscales noise.
BOX_MIN_HEIGHT: Final[float] = 0.25
#: Cropping to more than this share of the frame zooms in too little to help.
MAX_USEFUL_AREA: Final[float] = 0.5
#: Motion: a pixel moves when its mean frame difference is this many times
#: the frame's median difference, and at least this many grey levels.
MOTION_RATIO: Final[float] = 4.0
MOTION_MIN_LEVEL: Final[float] = 6.0
#: The moving blob must be at least this share of the frame to be a person.
MOTION_MIN_AREA: Final[float] = 0.0005


def _grow(x0: float, y0: float, x1: float, y1: float) -> CropBox:
    width, height = x1 - x0, y1 - y0
    x0, x1 = x0 - BOX_MARGIN * width, x1 + BOX_MARGIN * width
    y0, y1 = y0 - BOX_MARGIN * height, y1 + BOX_MARGIN * height
    if y1 - y0 < BOX_MIN_HEIGHT:
        centre = (y0 + y1) / 2.0
        y0, y1 = centre - BOX_MIN_HEIGHT / 2.0, centre + BOX_MIN_HEIGHT / 2.0
    if x1 - x0 < BOX_MIN_HEIGHT:
        centre = (x0 + x1) / 2.0
        x0, x1 = centre - BOX_MIN_HEIGHT / 2.0, centre + BOX_MIN_HEIGHT / 2.0
    return (max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1))


def box_area(box: CropBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def worth_cropping(box: CropBox | None) -> bool:
    """A crop helps only when it zooms in: a box covering most of the frame
    would give the detector nearly the same picture."""
    return box is not None and 0.0 < box_area(box) <= MAX_USEFUL_AREA


def landmark_box(
    landmarks: npt.NDArray[np.floating], detected: npt.NDArray[np.bool_]
) -> CropBox | None:
    """The grown box around every visible landmark of every detected frame,
    or None with no such landmark. ``landmarks`` is ``(T, 33, 4)``, x/y as
    fractions of the frame."""
    data = np.asarray(landmarks, dtype=np.float64)
    flags = np.asarray(detected, dtype=bool)
    if data.ndim != 3 or data.shape[0] == 0 or not flags.any():
        return None
    points = data[flags]
    seen = points[..., 3] >= BOX_MIN_VISIBILITY
    xs, ys = points[..., 0][seen], points[..., 1][seen]
    inside = (xs >= 0.0) & (xs <= 1.0) & (ys >= 0.0) & (ys <= 1.0)
    xs, ys = xs[inside], ys[inside]
    if xs.size == 0:
        return None
    return _grow(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))


def motion_box(mean_difference: npt.NDArray[np.floating]) -> CropBox | None:
    """The grown box around the largest moving region of a ``(H, W)`` map of
    mean absolute frame differences, or None when nothing moves enough."""
    energy = np.asarray(mean_difference, dtype=np.float32)
    if energy.ndim != 2 or energy.size == 0:
        return None
    height, width = energy.shape
    level = max(MOTION_MIN_LEVEL, MOTION_RATIO * float(np.median(energy)))
    mask = (energy >= level).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return None
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[best, cv2.CC_STAT_AREA] < MOTION_MIN_AREA * width * height:
        return None
    x, y = stats[best, cv2.CC_STAT_LEFT], stats[best, cv2.CC_STAT_TOP]
    w, h = stats[best, cv2.CC_STAT_WIDTH], stats[best, cv2.CC_STAT_HEIGHT]
    return _grow(x / width, y / height, (x + w) / width, (y + h) / height)


def crop_pixels(frame: npt.NDArray[np.uint8], box: CropBox) -> npt.NDArray[np.uint8]:
    """The part of ``frame`` inside ``box`` (at least one pixel each way)."""
    height, width = frame.shape[:2]
    x0 = min(int(box[0] * width), width - 1)
    y0 = min(int(box[1] * height), height - 1)
    x1 = max(int(round(box[2] * width)), x0 + 1)
    y1 = max(int(round(box[3] * height)), y0 + 1)
    return np.ascontiguousarray(frame[y0:y1, x0:x1])


def uncrop_landmarks(
    landmarks: npt.NDArray[np.floating], box: CropBox
) -> npt.NDArray[np.float32]:
    """``(..., 33, 4)`` landmarks normalised to the crop, mapped to the full
    frame. z is MediaPipe's depth on the crop's x scale, so it scales with
    the crop width; visibility is unchanged."""
    data = np.array(landmarks, dtype=np.float32, copy=True)
    x0, y0, x1, y1 = box
    data[..., 0] = x0 + data[..., 0] * (x1 - x0)
    data[..., 1] = y0 + data[..., 1] * (y1 - y0)
    data[..., 2] = data[..., 2] * (x1 - x0)
    return data


__all__ = [
    "CropBox",
    "box_area",
    "crop_pixels",
    "landmark_box",
    "motion_box",
    "uncrop_landmarks",
    "worth_cropping",
]
