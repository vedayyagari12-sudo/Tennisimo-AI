"""Per-frame ball candidates for Court mode (design 4.3). PURE-CV.

Arrays in, numpy records out. Never opens a file, never reads a clock, no
randomness: the same frames give the same records. ``cv2`` is used only as an
image library on arrays it is handed. Frame acquisition is
``flight_frames.py``; linking is the pure ``analysis/flight_track.py``.

Built BESIDE the single-swing detector, not on it (design 4.1): its fixed
background, 18-level threshold, 12 px^2 floor and 3x3 open all erase a
2-3 px far-court ball. ``detector.py`` is reused, unmodified, for
``colour_mask``, ``contour_features``, ``classify_contour`` and its area cap.

* **Motion: double difference.** (|I_t - I_t-1| > tau) AND (|I_t+1 - I_t| > tau),
  tau = max(8, 4 x MAD of that signed frame difference). No background model, so
  lighting drift does not accumulate, and the trailing ghost is removed.
* **No open step.** A 2x2 close only, built so it does not shift the blob (the
  default OpenCV anchor moves a 2x2 closing by one pixel, which is a full ball
  radius at this size).
* **Tiny blobs (brief G2).** ``contour_features`` returns None for a zero-area
  polygon, which drops 2-3 px balls. Below ``TINY_DIAMETER_PX`` expected
  diameter, or with the size unknown and at most ``TINY_UNKNOWN_MAX_AREA_PX``
  pixels, features come from ``connectedComponentsWithStats`` and the second
  moments of the pixel coordinates. Above it, the existing round/streak tiers.
* **Colour is a score** below ``COLOUR_GATE_DIAMETER_PX`` (chroma subsampling
  erases it), and a gate at or above it when the expected size is known.
* **Size.** Rejected when the minor axis exceeds 2.5 x expected + 2 px.

Pixel units are TRACK_SPACE (``flight_frames.TRACK_SPACE_LONG_EDGE_PX``), y
DOWN. A calibration passed here must be in the same pixel space.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.court_calibration import CourtCalibration
from app.analysis.court_model import DOUBLES_HALF_WIDTH_M, HALF_LENGTH_M
from app.analysis.flight_track import FLIGHT_CANDIDATE_DTYPE
from app.analysis.homography import BALL_RADIUS_M, image_scale_px_per_m, project_points_3d
from app.ball.detector import (
    TIER_B_AREA_MAX_PX2,
    classify_contour,
    colour_mask,
    contour_features,
)
from app.ball.track import TIER_REJECTED

__all__ = [
    "FLIGHT_CANDIDATE_DTYPE",
    "court_roi_mask",
    "double_difference_mask",
    "expected_diameter_fn",
    "find_flight_candidates",
]

U8Array = npt.NDArray[np.uint8]

#: Design 4.3: tau = max(8, 4 x MAD of the frame difference).
MOTION_DIFF_FLOOR: Final[int] = 8
MOTION_DIFF_MAD_FACTOR: Final[float] = 4.0
#: A candidate's peak motion strength must reach this multiple of the frame's
#: motion threshold. T2 (noise sigma 3, JPEG 60-85, drift) at 1080p: clutter
#: has median strength 12 and P95 22 over a threshold of 8, the ball P1 18
#: (far) and 81 (near). 2.5x keeps the ball and drops most of the clutter.
#: Relative to the measured noise floor, so it scales with real footage.
MIN_CONTRAST_FACTOR: Final[float] = 2.5
#: Design 4.3: a 2x2 close, no open.
CLOSE_KERNEL: Final[U8Array] = np.ones((2, 2), dtype=np.uint8)
#: Paired anchors that make dilate-then-erode a true, shift-free closing.
_DILATE_ANCHOR: Final[tuple[int, int]] = (0, 0)
_ERODE_ANCHOR: Final[tuple[int, int]] = (1, 1)

#: Design 4.3 shape limits, applied to the component's pixel count (brief G2).
MIN_AREA_PX2: Final[int] = 2
MAX_AREA_PX2: Final[float] = TIER_B_AREA_MAX_PX2
#: Brief G2: below this expected diameter, moment features, not contours.
TINY_DIAMETER_PX: Final[float] = 6.0
#: Brief G2: with the size unknown, components up to this many pixels are tiny.
TINY_UNKNOWN_MAX_AREA_PX: Final[int] = 30
#: Design 4.3: reject when minor > 2.5 x expected + 2 px (a ball in the air is
#: nearer than the ground point behind it, hence the loose factor).
SIZE_GATE_FACTOR: Final[float] = 2.5
SIZE_GATE_MARGIN_PX: Final[float] = 2.0
#: Design 4.3: colour is only a score below this expected diameter.
COLOUR_GATE_DIAMETER_PX: Final[float] = 4.0
#: At or above COLOUR_GATE_DIAMETER_PX, at least this fraction of the moving
#: pixels must pass ``colour_mask``. Starting value; tune on T1/T2 only.
COLOUR_GATE_MIN_SCORE: Final[float] = 0.2
#: Precision first: a false landing on the shot map misleads; a missed one is
#: only absent. T2 1080p, 4 seeds: this setting gave bounce precision 1.00 /
#: recall 0.50; the looser (5 px, 0.1) gave 0.69 / 0.55. Neither meets the
#: design 11.2 T2 targets; real footage decides (synthetic-tuned placeholder).
OWN_SIZE_COLOUR_GATE_PX: Final[float] = 4.0
OWN_SIZE_COLOUR_MIN_SCORE: Final[float] = 0.2

#: Brief Constants / design 4.3: court polygon plus 4 m run-off ...
RUNOFF_M: Final[float] = 4.0
#: ... extended upward in the image by the projection of a 7 m height.
ROI_HEIGHT_M: Final[float] = 7.0
BALL_DIAMETER_M: Final[float] = 2.0 * BALL_RADIUS_M

_FILL_SHIFT_BITS: Final[int] = 4
_ROI_COORD_LIMIT_PX: Final[float] = 1e6
_GREY_LEVELS: Final[int] = 256


def _mad_threshold(signed_diff: npt.NDArray[np.int16]) -> int:
    """max(floor, factor x MAD) of a signed difference image, exactly, via an
    integer histogram (no float sort over millions of pixels)."""
    offset = _GREY_LEVELS - 1
    histogram = np.bincount((signed_diff.ravel() + offset).astype(np.intp), minlength=2 * offset + 1)
    total = int(histogram.sum())
    half = (total + 1) // 2
    median = int(np.searchsorted(np.cumsum(histogram), half)) - offset
    values = np.arange(-offset, offset + 1)
    deviation_hist = np.bincount(np.abs(values - median), weights=histogram, minlength=2 * offset + 1)
    mad = int(np.searchsorted(np.cumsum(deviation_hist), half))
    return max(MOTION_DIFF_FLOOR, int(math.ceil(MOTION_DIFF_MAD_FACTOR * mad)))


def _close_2x2(mask: U8Array) -> U8Array:
    dilated = cv2.dilate(mask, CLOSE_KERNEL, anchor=_DILATE_ANCHOR)
    return np.asarray(cv2.erode(dilated, CLOSE_KERNEL, anchor=_ERODE_ANCHOR), dtype=np.uint8)


def _double_difference(
    prev_gray: U8Array, cur_gray: U8Array, next_gray: U8Array
) -> tuple[U8Array, U8Array, int]:
    """(closed mask 0/255, strength = min(|d_before|, |d_after|), threshold)."""
    if not (prev_gray.shape == cur_gray.shape == next_gray.shape) or cur_gray.ndim != 2:
        raise ValueError("expected three single-channel frames of one shape")
    before = cur_gray.astype(np.int16) - prev_gray.astype(np.int16)
    after = next_gray.astype(np.int16) - cur_gray.astype(np.int16)
    abs_before = np.abs(before).astype(np.uint8)
    abs_after = np.abs(after).astype(np.uint8)
    threshold_before, threshold_after = _mad_threshold(before), _mad_threshold(after)
    moving = (abs_before > threshold_before) & (abs_after > threshold_after)
    mask = _close_2x2(moving.astype(np.uint8) * 255)
    return mask, np.minimum(abs_before, abs_after), max(threshold_before, threshold_after)


def double_difference_mask(prev_gray: U8Array, cur_gray: U8Array, next_gray: U8Array) -> U8Array:
    """Pixels that changed both into and out of ``cur_gray``, 2x2 closed, 0/255."""
    return _double_difference(prev_gray, cur_gray, next_gray)[0]


def _moment_features(
    component: npt.NDArray[np.bool_],
) -> tuple[float, float, float]:
    """(major, minor, orientation_deg) from pixel-coordinate second moments.

    Axis length along a principal direction is sqrt(12 lambda + 1), exact for a
    run of whole pixels: a 2x1 blob is 2 by 1, a 2x2 blob is 2 by 2.
    """
    ys, xs = np.nonzero(component)
    dx = xs - xs.mean()
    dy = ys - ys.mean()
    sxx = float(np.mean(dx * dx))
    syy = float(np.mean(dy * dy))
    sxy = float(np.mean(dx * dy))
    spread = math.hypot(0.5 * (sxx - syy), sxy)
    centre = 0.5 * (sxx + syy)
    major = math.sqrt(12.0 * (centre + spread) + 1.0)
    minor = math.sqrt(max(12.0 * (centre - spread), 0.0) + 1.0)
    orientation = math.degrees(0.5 * math.atan2(2.0 * sxy, sxx - syy)) % 180.0
    return major, minor, orientation


def _contour_shape(component: npt.NDArray[np.bool_]) -> tuple[float, float, float] | None:
    """(major, minor, orientation_deg) via the existing tiers, or None when the
    contour is degenerate or rejected by ``classify_contour``."""
    contours, _ = cv2.findContours(
        component.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    largest = max(contours, key=lambda c: (cv2.contourArea(c), -len(c)))
    features = contour_features(largest)
    if features is None or classify_contour(features) == TIER_REJECTED:
        return None
    return features.major_axis_px, features.minor_axis_px, features.orientation_deg


def find_flight_candidates(
    prev_gray: U8Array,
    cur_gray: U8Array,
    next_gray: U8Array,
    cur_bgr: U8Array,
    frame_index: int,
    time_s: float,
    roi_mask: U8Array | None = None,
    expected_diameter_px: Callable[[float, float], float] | None = None,
    max_diameter_px: Callable[[float, float], float] | None = None,
) -> npt.NDArray[np.void]:
    """Ball candidates in ``cur``, as ``FLIGHT_CANDIDATE_DTYPE`` records in
    raster order of the connected components.

    ``roi_mask`` (0/255, frame-sized) limits where motion counts.
    ``expected_diameter_px(x, y)`` is the ball's expected diameter in pixels at
    an image point, or None when there is no calibration.
    ``max_diameter_px(x, y)`` is the largest it could be there (an airborne
    ball is nearer than the ground point); the size gate uses it when given.
    """
    mask, strength, threshold = _double_difference(prev_gray, cur_gray, next_gray)
    min_contrast = MIN_CONTRAST_FACTOR * threshold
    if roi_mask is not None:
        mask = cv2.bitwise_and(mask, roi_mask)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8, ltype=cv2.CV_32S
    )
    records: list[tuple[int, float, float, float, float, float, float, float, float, float]] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < MIN_AREA_PX2 or area > MAX_AREA_PX2:
            continue
        x0 = int(stats[label, cv2.CC_STAT_LEFT])
        y0 = int(stats[label, cv2.CC_STAT_TOP])
        x1 = x0 + int(stats[label, cv2.CC_STAT_WIDTH])
        y1 = y0 + int(stats[label, cv2.CC_STAT_HEIGHT])
        component = labels[y0:y1, x0:x1] == label
        cx, cy = float(centroids[label, 0]), float(centroids[label, 1])

        diameter: float | None = None
        if expected_diameter_px is not None:
            value = float(expected_diameter_px(cx, cy))
            diameter = value if math.isfinite(value) and value > 0.0 else None
        tiny = diameter < TINY_DIAMETER_PX if diameter is not None else (
            area <= TINY_UNKNOWN_MAX_AREA_PX
        )
        shape = _moment_features(component) if tiny else _contour_shape(component)
        if shape is None:
            continue
        major, minor, orientation = shape
        ceiling = diameter
        if max_diameter_px is not None and diameter is not None:
            largest = float(max_diameter_px(cx, cy))
            if math.isfinite(largest) and largest > diameter:
                ceiling = largest
        if ceiling is not None and minor > SIZE_GATE_FACTOR * ceiling + SIZE_GATE_MARGIN_PX:
            continue

        colour = colour_mask(np.ascontiguousarray(cur_bgr[y0:y1, x0:x1]))
        colour_score = float(np.count_nonzero(colour[component])) / float(area)
        if (
            diameter is not None
            and diameter >= COLOUR_GATE_DIAMETER_PX
            and colour_score < COLOUR_GATE_MIN_SCORE
        ):
            continue
        # Also judge colour once the blob ITSELF is big enough to have one:
        # far-court clutter from a player's edges is 5+ px across where the
        # ground-plane estimate says 2, and passed (82 % of such clutter scores
        # under 0.2 against 6 % of ball blobs, T2 seed 20270001).
        if minor >= OWN_SIZE_COLOUR_GATE_PX and colour_score < OWN_SIZE_COLOUR_MIN_SCORE:
            continue
        contrast = float(np.max(strength[y0:y1, x0:x1][component]))
        if contrast < min_contrast:
            continue
        records.append(
            (int(frame_index), float(time_s), cx, cy, float(area), minor, major, orientation,
             colour_score, contrast)
        )
    return np.array(records, dtype=FLIGHT_CANDIDATE_DTYPE)


def expected_diameter_fn(calibration: CourtCalibration) -> Callable[[float, float], float]:
    """Expected ball diameter at an image point: f x 0.067 / range to the
    ground point under it (design 4.3), in the calibration's pixel space."""
    pose = calibration.pose
    h_ic = calibration.h_image_to_court

    def diameter(u_px: float, v_px: float) -> float:
        return BALL_DIAMETER_M * image_scale_px_per_m(pose, h_ic, (u_px, v_px))

    return diameter


def max_diameter_fn(calibration: CourtCalibration) -> Callable[[float, float], float]:
    """The LARGEST diameter a ball at an image point could have: f x 0.067 /
    range to the NEAREST point of its camera ray inside the playing volume
    (the court plus ``RUNOFF_M``, up to ``ROI_HEIGHT_M``).

    ``expected_diameter_fn`` assumes the ball is on the ground. An airborne
    ball sits between the camera and that ground point, so it is bigger: a
    ball 2.8 m up over the net measured 6.4 px against 1.2 px expected, and the
    size gate threw it away for 40 consecutive frames (T2 seed 20270000). An
    upper gate must use the nearest plausible point, not the ground.
    """
    pose = calibration.pose
    m = pose.projection_matrix
    centre = np.asarray(pose.position_m, dtype=np.float64)
    near_y = -(HALF_LENGTH_M + RUNOFF_M)
    far_y = HALF_LENGTH_M + RUNOFF_M
    ground = expected_diameter_fn(calibration)

    def diameter(u_px: float, v_px: float) -> float:
        direction = np.linalg.solve(m[:, :3], np.array([u_px, v_px, 1.0]))
        norm = float(np.linalg.norm(direction))
        if norm == 0.0:
            return ground(u_px, v_px)
        direction = direction / norm
        if direction[1] < 0.0:  # pointing away from the court: use its sign
            direction = -direction
        # Smallest reach along the ray that is inside the volume.
        reach = 0.0
        if centre[1] < near_y and direction[1] > 0.0:
            reach = max(reach, (near_y - centre[1]) / direction[1])
        if centre[2] > ROI_HEIGHT_M and direction[2] < 0.0:
            reach = max(reach, (ROI_HEIGHT_M - centre[2]) / direction[2])
        if reach <= 0.0 or not math.isfinite(reach):
            return ground(u_px, v_px)
        point = centre + reach * direction
        if point[1] > far_y:
            return ground(u_px, v_px)
        return max(pose.focal_px * BALL_DIAMETER_M / reach, ground(u_px, v_px))

    return diameter


def court_roi_mask(calibration: CourtCalibration, frame_size: tuple[int, int]) -> U8Array:
    """0/255 mask of the court plus ``RUNOFF_M`` run-off, extended upward by
    the image of a ``ROI_HEIGHT_M`` box over it, on a ``(width, height)`` frame.

    The calibration's pixels are rescaled to ``frame_size``. When any box
    corner is behind the camera the projected hull is meaningless, so the
    whole frame is returned (no ROI, never a wrongly clipped one).
    """
    width, height = frame_size
    half_x = DOUBLES_HALF_WIDTH_M + RUNOFF_M
    half_y = HALF_LENGTH_M + RUNOFF_M
    corners = np.array(
        [[-half_x, -half_y], [half_x, -half_y], [half_x, half_y], [-half_x, half_y]],
        dtype=np.float64,
    )
    box = np.vstack(
        [np.column_stack([corners, np.zeros(4)]), np.column_stack([corners, np.full(4, ROI_HEIGHT_M)])]
    )
    projection = calibration.pose.projection_matrix
    depth = np.column_stack([box, np.ones(len(box))]) @ projection[2]
    mask = np.zeros((height, width), dtype=np.uint8)
    if np.any(depth <= 0.0):
        mask[:] = 255
        return mask
    uv = project_points_3d(calibration.pose, box)
    cal_width, cal_height = calibration.frame_size
    uv = uv * np.array([width / cal_width, height / cal_height])
    uv = np.clip(uv, -_ROI_COORD_LIMIT_PX, _ROI_COORD_LIMIT_PX)
    fixed = np.round(uv * (1 << _FILL_SHIFT_BITS)).astype(np.int32)
    hull = cv2.convexHull(fixed.reshape(-1, 1, 2))
    cv2.fillConvexPoly(mask, hull, 255, cv2.LINE_8, _FILL_SHIFT_BITS)
    return mask
