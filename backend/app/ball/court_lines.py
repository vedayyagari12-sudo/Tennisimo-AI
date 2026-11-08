"""Court line segments for the optional court fit (step 19). PURE-CV: cv2 and
numpy on in-memory frames, no I/O.

White top-hat (bright, thin structures on a darker surface), a fixed
threshold, then ``cv2.HoughLinesP``, all at a 1280 px long edge so the
constants mean the same thing at any resolution; segments return in the
caller's pixels.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

WORK_LONG_EDGE_PX: Final[int] = 1280
TOPHAT_KERNEL_PX: Final[int] = 15
LINE_THRESHOLD: Final[int] = 40
HOUGH_VOTES: Final[int] = 40
MIN_SEGMENT_PX: Final[int] = 30
MAX_GAP_PX: Final[int] = 8
REFINE_MIN_SEGMENT_PX: Final[int] = 8
REFINE_MIN_ANGLE_DEG: Final[float] = 15.0

U8 = npt.NDArray[np.uint8]


def _line_mask(gray: U8) -> U8:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (TOPHAT_KERNEL_PX, TOPHAT_KERNEL_PX))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    _, mask = cv2.threshold(tophat, LINE_THRESHOLD, 255, cv2.THRESH_BINARY)
    return np.asarray(mask, dtype=np.uint8)


def detect_line_segments(frame_bgr: U8) -> npt.NDArray[np.float64]:
    """(N, 4) segments ``x1, y1, x2, y2`` in the frame's pixels."""
    height, width = frame_bgr.shape[:2]
    scale = WORK_LONG_EDGE_PX / float(max(width, height))
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if scale != 1.0:
        gray = cv2.resize(gray, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    lines = cv2.HoughLinesP(
        _line_mask(gray), 1, np.pi / 360.0, HOUGH_VOTES,
        minLineLength=MIN_SEGMENT_PX, maxLineGap=MAX_GAP_PX,
    )
    if lines is None:
        return np.zeros((0, 4), dtype=np.float64)
    return np.asarray(lines.reshape(-1, 4), dtype=np.float64) / scale


def refine_tap_to_intersection(
    frame_gray: U8, u_px: float, v_px: float, *, window_px: int = 20
) -> tuple[float, float] | None:
    """Snap a tap to the crossing of the two line edges near it, or None.

    Only within ``window_px`` of the tap, and only when two clearly different
    directions are found: a tap is never moved to somewhere it did not mean.
    """
    height, width = frame_gray.shape[:2]
    x0, y0 = max(int(u_px) - window_px, 0), max(int(v_px) - window_px, 0)
    x1, y1 = min(int(u_px) + window_px + 1, width), min(int(v_px) + window_px + 1, height)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return None
    patch = np.ascontiguousarray(frame_gray[y0:y1, x0:x1])
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (TOPHAT_KERNEL_PX, TOPHAT_KERNEL_PX))
    _, mask = cv2.threshold(cv2.morphologyEx(patch, cv2.MORPH_TOPHAT, kernel), LINE_THRESHOLD, 255, cv2.THRESH_BINARY)
    found = cv2.HoughLinesP(mask, 1, np.pi / 180.0, 8, minLineLength=REFINE_MIN_SEGMENT_PX, maxLineGap=3)
    if found is None:
        return None
    segments = found.reshape(-1, 4).astype(np.float64)
    lengths = np.hypot(segments[:, 2] - segments[:, 0], segments[:, 3] - segments[:, 1])
    angles = np.degrees(np.arctan2(segments[:, 3] - segments[:, 1], segments[:, 2] - segments[:, 0])) % 180.0
    first = int(np.argmax(lengths))
    diff = np.abs(angles - angles[first]) % 180.0
    other = np.minimum(diff, 180.0 - diff) >= REFINE_MIN_ANGLE_DEG
    if not other.any():
        return None
    second = int(np.flatnonzero(other)[np.argmax(lengths[other])])

    def homogeneous(s: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        return np.cross([s[0], s[1], 1.0], [s[2], s[3], 1.0])

    point = np.cross(homogeneous(segments[first]), homogeneous(segments[second]))
    if abs(point[2]) < 1e-9:
        return None
    x, y = point[0] / point[2] + x0, point[1] / point[2] + y0
    if abs(x - u_px) > window_px or abs(y - v_px) > window_px:
        return None
    return float(x), float(y)
