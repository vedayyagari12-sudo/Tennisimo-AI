"""Court line segments for the optional court fit (step 19). PURE-CV: cv2 and
numpy on in-memory frames, no I/O.

White top-hat (bright, thin structures on a darker surface), a fixed
threshold, then ``cv2.HoughLinesP``, all at a 1280 px long edge so the
constants mean the same thing at any resolution; segments return in the
caller's pixels.

``fit_court_from_frame`` runs the fit as a cascade (measured on TrackNet
broadcast first frames, 2026-11-09). A later stage runs only when the earlier
ones found nothing, so a frame that fitted before fits exactly as before:

1. Every segment at ``LINE_THRESHOLD`` (fits 7 of 10 matches).
2. Only segments on the court surface: the convex hull of the largest
   court-coloured region, grown by ``SURFACE_GROW_PX``. Long edges in the
   stands otherwise take the 6-longest-lines slots (game 6, clay: now fits
   at support 0.74; game 5, green: 0.74). Not first, because on a two-tone
   court (US Open: blue inside, dark outside) the hull loses the doubles
   lines.
3. The same at ``FAINT_LINE_THRESHOLD``, for dusty lines (game 6: 0.92).
   Not in stage 2: it costs game 5 its fit.
4. Stages 2-3 again with the surface closed across painted lines first,
   for when a service line cuts the court colour in two (rendered clay
   court with stands: 0.98). Not in stages 2-3: closing joins game 5's
   court to its stands.

``MIN_SUPPORT`` is the same in every stage. Game 4 (clay, far baseline
almost invisible) still finds nothing: its correct fit scores 0.54.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.court_fit import CourtFitCandidate, fit_court_candidates

WORK_LONG_EDGE_PX: Final[int] = 1280
TOPHAT_KERNEL_PX: Final[int] = 15
LINE_THRESHOLD: Final[int] = 40
HOUGH_VOTES: Final[int] = 40
MIN_SEGMENT_PX: Final[int] = 30
MAX_GAP_PX: Final[int] = 8
REFINE_MIN_SEGMENT_PX: Final[int] = 8
REFINE_MIN_ANGLE_DEG: Final[float] = 15.0
FAINT_LINE_THRESHOLD: Final[int] = 30
#: Court colour is the median a*b* (CIELAB) of the frame's centre band; a
#: pixel within this distance of it is court.
SURFACE_AB_TOLERANCE: Final[float] = 14.0
SURFACE_BAND: Final[tuple[float, float, float, float]] = (0.25, 0.45, 0.75, 0.85)  # x0, y0, x1, y1
SURFACE_GROW_PX: Final[int] = 30
SURFACE_CLOSE_FRACTION: Final[float] = 0.015
#: A surface outside this share of the frame is not trusted as a court; the
#: surface stages are skipped.
SURFACE_FRACTION_RANGE: Final[tuple[float, float]] = (0.15, 0.97)

U8 = npt.NDArray[np.uint8]


def _line_mask(gray: U8, threshold: int = LINE_THRESHOLD) -> U8:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (TOPHAT_KERNEL_PX, TOPHAT_KERNEL_PX))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    _, mask = cv2.threshold(tophat, threshold, 255, cv2.THRESH_BINARY)
    return np.asarray(mask, dtype=np.uint8)


def court_surface_mask(frame_bgr: U8, *, close_lines: bool = False) -> U8 | None:
    """255 on the court surface (the grown convex hull of the largest
    court-coloured region), or None when that covers an implausible share of
    the frame. ``close_lines`` first closes the colour mask across painted
    lines."""
    height, width = frame_bgr.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(frame_bgr, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    x0, y0, x1, y1 = SURFACE_BAND
    band = lab[int(height * y0):int(height * y1), int(width * x0):int(width * x1)].reshape(-1, 3)
    if band.size == 0:
        return None
    reference = np.median(band, axis=0)
    distance = np.hypot(lab[..., 1] - reference[1], lab[..., 2] - reference[2])
    mask = np.where(distance <= SURFACE_AB_TOLERANCE, 255, 0).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    if close_lines:
        across = max(3, round(SURFACE_CLOSE_FRACTION * max(width, height))) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((across, across), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return None
    # Only the largest piece: keeping every large one let court-coloured
    # stands into the hull (game 5). A far half cut off by the net is
    # reached by the grow instead.
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    points = cv2.findNonZero((labels == largest).astype(np.uint8))
    surface = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(surface, cv2.convexHull(points), 255)
    grow = 2 * SURFACE_GROW_PX + 1
    surface = cv2.dilate(surface, np.ones((grow, grow), np.uint8))
    low, high = SURFACE_FRACTION_RANGE
    if not low <= float(surface.mean()) / 255.0 <= high:
        return None
    return np.asarray(surface, dtype=np.uint8)


def segments_on_surface(segments: npt.NDArray[np.float64], surface: U8) -> npt.NDArray[np.float64]:
    """The segments whose two ends and midpoint all lie on ``surface``."""
    if len(segments) == 0:
        return segments
    height, width = surface.shape[:2]
    keep = np.ones(len(segments), dtype=bool)
    for x, y in (
        (segments[:, 0], segments[:, 1]),
        (segments[:, 2], segments[:, 3]),
        ((segments[:, 0] + segments[:, 2]) / 2.0, (segments[:, 1] + segments[:, 3]) / 2.0),
    ):
        cols = np.clip(x.astype(int), 0, width - 1)
        rows = np.clip(y.astype(int), 0, height - 1)
        keep &= surface[rows, cols] > 0
    return segments[keep]


def fit_court_from_frame(frame_bgr: U8, *, max_candidates: int = 3) -> tuple[CourtFitCandidate, ...]:
    """The step 19 court fit on one frame, as the cascade in the module doc."""
    height, width = frame_bgr.shape[:2]
    size = (width, height)
    fits = fit_court_candidates(detect_line_segments(frame_bgr), size, max_candidates=max_candidates)
    if fits:
        return fits
    for close_lines in (False, True):
        surface = court_surface_mask(frame_bgr, close_lines=close_lines)
        if surface is None:
            continue
        for threshold in (LINE_THRESHOLD, FAINT_LINE_THRESHOLD):
            segments = segments_on_surface(detect_line_segments(frame_bgr, threshold=threshold), surface)
            fits = fit_court_candidates(segments, size, max_candidates=max_candidates)
            if fits:
                return fits
    return ()


def detect_line_segments(frame_bgr: U8, *, threshold: int = LINE_THRESHOLD) -> npt.NDArray[np.float64]:
    """(N, 4) segments ``x1, y1, x2, y2`` in the frame's pixels."""
    height, width = frame_bgr.shape[:2]
    scale = WORK_LONG_EDGE_PX / float(max(width, height))
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if scale != 1.0:
        gray = cv2.resize(gray, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    lines = cv2.HoughLinesP(
        _line_mask(gray, threshold), 1, np.pi / 360.0, HOUGH_VOTES,
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
