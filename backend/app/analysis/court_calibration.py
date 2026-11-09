"""Court calibration from user taps: validation, gating, fit (design 3.2-3.4).

PURE: taps in, a verdict out. Every threshold is a ``Final`` constant.

Pixel units. Taps are pixels of the frame they were tapped on (normalised taps
are converted by the caller). The pixel gates (8 px, 12 px, RMS 2 / max 4, f
400-3000) are 1280-EQUIVALENT (brief, Constants): a measured distance is
multiplied by ``1280 / long_edge`` before it is compared. ``sigma_tap_px`` and
the stored reprojection errors are in the frame's own pixels.

Tap counts (3.2, amended by brief G21): fewer than 4 is rejected; 4 to 7 is
shot map and zones only, flagged ``unverified_calibration``; 8 or more allows
line calls. 4 points fit exactly, so a zero reprojection error proves nothing.
With the recommended 6, OpenCV k1 = -0.25 lens distortion still passes the
reprojection gates (RMS 1.94 / max 3.93 px at 1280-equivalent) while leaving
up to 0.47 m of systematic ground error; with 8 or more taps the same
distortion is rejected. The reprojection gate itself still runs from 6 taps.

Gate order: frame shape, too few, duplicates, collinear, hull size, mirrored
(these six are ``validate_taps``, except frame shape), then conditioning,
reprojection and camera pose.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.analysis.court_model import CourtEnd, CourtKeypoint, keypoint_xy
from app.analysis.homography import (
    CameraPose,
    Float64Array,
    decompose_camera_pose,
    fit_homography_dlt,
    invert_homography,
    reprojection_errors_px,
)
from app.models.court import CalibrationRejection

__all__ = [
    "CALIBRATION_VERSION",
    "CalibrationRejection",
    "CalibrationVerdict",
    "CourtCalibration",
    "CourtTap",
    "calibrate_court",
    "validate_taps",
]

CALIBRATION_VERSION: Final[str] = "court_cal_v1"

MIN_TAPS: Final[int] = 4
#: The reprojection gate needs an over-determined fit (3.4: N >= 6).
REPROJECTION_GATE_MIN_TAPS: Final[int] = 6
#: Line calls need enough taps that lens distortion cannot hide (brief G21).
CALLS_MIN_TAPS: Final[int] = 8
#: Long edge the pixel gates are expressed at.
REFERENCE_LONG_EDGE_PX: Final[int] = 1280
DUPLICATE_TAP_PX: Final[float] = 8.0
COLLINEAR_TAP_PX: Final[float] = 12.0
MIN_HULL_FRAME_FRACTION: Final[float] = 0.05
#: Brief G1: s[7] / s[0] of the normalised A.
MIN_DLT_CONDITION: Final[float] = 1e-3
MAX_REPROJECTION_RMS_PX: Final[float] = 2.0
MAX_REPROJECTION_PX: Final[float] = 4.0
#: Relative aspect mismatch between the tapped frame and the decoded video.
FRAME_ASPECT_TOLERANCE: Final[float] = 0.02
FOCAL_MIN_PX: Final[float] = 400.0
FOCAL_MAX_PX: Final[float] = 3000.0
CAMERA_HEIGHT_MIN_M: Final[float] = 0.5
CAMERA_HEIGHT_MAX_M: Final[float] = 15.0
#: Default tap error with a magnifier loupe (3.4, estimate).
DEFAULT_SIGMA_TAP_PX: Final[float] = 1.5
#: Court-space triangles smaller than this carry no orientation information.
MIN_ORIENTATION_AREA_M2: Final[float] = 1.0
UNVERIFIED_CALIBRATION_FLAG: Final[str] = "unverified_calibration"


@dataclass(frozen=True)
class CourtTap:
    keypoint: CourtKeypoint
    u_px: float
    v_px: float


@dataclass(frozen=True, eq=False)
class CourtCalibration:
    h_court_to_image: Float64Array
    h_image_to_court: Float64Array
    pose: CameraPose
    frame_size: tuple[int, int]
    taps: tuple[CourtTap, ...]
    reprojection_rms_px: float
    reprojection_max_px: float
    sigma_tap_px: float
    calls_allowed: bool
    flags: tuple[str, ...]
    version: str


@dataclass(frozen=True, eq=False)
class CalibrationVerdict:
    calibration: CourtCalibration | None
    rejection: CalibrationRejection | None


def _to_1280(frame_size: tuple[int, int]) -> float:
    """Factor converting this frame's pixels to 1280-equivalent pixels."""
    return REFERENCE_LONG_EDGE_PX / float(max(frame_size))


def _signed_area(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return 0.5 * ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def _triangle_height(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    """Height over the longest side: how far the triple is from collinear."""
    longest = max(math.dist(a, b), math.dist(b, c), math.dist(a, c))
    if longest == 0.0:
        return 0.0
    return 2.0 * abs(_signed_area(a, b, c)) / longest


def _hull_area(points: Sequence[tuple[float, float]]) -> float:
    """Convex hull area by Andrew's monotone chain and the shoelace formula."""
    unique = sorted(set(points))
    if len(unique) < 3:
        return 0.0

    def half(sequence: list[tuple[float, float]]) -> list[tuple[float, float]]:
        chain: list[tuple[float, float]] = []
        for point in sequence:
            while len(chain) >= 2 and _signed_area(chain[-2], chain[-1], point) <= 0.0:
                chain.pop()
            chain.append(point)
        return chain

    lower = half(unique)
    upper = half(list(reversed(unique)))
    hull = lower[:-1] + upper[:-1]
    return 0.5 * abs(
        sum(
            hull[i][0] * hull[(i + 1) % len(hull)][1] - hull[(i + 1) % len(hull)][0] * hull[i][1]
            for i in range(len(hull))
        )
    )


def _widest_four(points: Sequence[tuple[float, float]]) -> tuple[int, ...]:
    """Indices of the 4 taps spanning the largest hull."""
    return max(
        itertools.combinations(range(len(points)), 4),
        key=lambda combo: _hull_area([points[i] for i in combo]),
    )


def _end_of(keypoint: CourtKeypoint) -> CourtEnd:
    return CourtEnd.NEAR if keypoint_xy(keypoint)[1] < 0.0 else CourtEnd.FAR


def validate_taps(
    taps: Sequence[CourtTap], frame_size: tuple[int, int]
) -> CalibrationRejection | None:
    """Geometry-only gates on the taps, before any fit. None means they pass."""
    if len(taps) < MIN_TAPS:
        return CalibrationRejection.TOO_FEW_TAPS
    to_1280 = _to_1280(frame_size)
    image = [(t.u_px, t.v_px) for t in taps]

    if len({t.keypoint for t in taps}) != len(taps):
        return CalibrationRejection.DUPLICATE_TAPS
    for a, b in itertools.combinations(image, 2):
        if math.dist(a, b) * to_1280 < DUPLICATE_TAP_PX:
            return CalibrationRejection.DUPLICATE_TAPS

    widest = [image[i] for i in _widest_four(image)]
    for a, b, c in itertools.combinations(widest, 3):
        if _triangle_height(a, b, c) * to_1280 < COLLINEAR_TAP_PX:
            return CalibrationRejection.COLLINEAR_TAPS

    width, height = frame_size
    if _hull_area(image) < MIN_HULL_FRAME_FRACTION * width * height:
        return CalibrationRejection.TAPS_TOO_SMALL

    if _is_mirrored(taps):
        return CalibrationRejection.TAPS_MIRRORED
    return None


def _is_mirrored(taps: Sequence[CourtTap]) -> bool:
    """Near drawn above far, or the court's handedness flipped.

    Court (x, y) maps to image (u, -v) with orientation preserved for any
    upright camera, so every well-shaped triple must keep its winding.
    """
    near_v = [t.v_px for t in taps if _end_of(t.keypoint) is CourtEnd.NEAR]
    far_v = [t.v_px for t in taps if _end_of(t.keypoint) is CourtEnd.FAR]
    if near_v and far_v and sum(near_v) / len(near_v) <= sum(far_v) / len(far_v):
        return True
    for a, b, c in itertools.combinations(taps, 3):
        court_area = _signed_area(keypoint_xy(a.keypoint), keypoint_xy(b.keypoint), keypoint_xy(c.keypoint))
        if abs(court_area) < MIN_ORIENTATION_AREA_M2:
            continue
        image_area = _signed_area((a.u_px, -a.v_px), (b.u_px, -b.v_px), (c.u_px, -c.v_px))
        if image_area * court_area < 0.0:
            return True
    return False


def _frame_shape_mismatch(frame_size: tuple[int, int], video_frame_size: tuple[int, int]) -> bool:
    if min(*frame_size, *video_frame_size) <= 0:
        return True
    tapped = frame_size[0] / frame_size[1]
    decoded = video_frame_size[0] / video_frame_size[1]
    return abs(tapped - decoded) > FRAME_ASPECT_TOLERANCE * decoded


def _pose_plausible(pose: CameraPose | None, frame_size: tuple[int, int]) -> bool:
    if pose is None:
        return False
    focal_1280 = pose.focal_px * _to_1280(frame_size)
    return (
        FOCAL_MIN_PX <= focal_1280 <= FOCAL_MAX_PX
        and CAMERA_HEIGHT_MIN_M <= pose.height_m <= CAMERA_HEIGHT_MAX_M
    )


def _reject(reason: CalibrationRejection) -> CalibrationVerdict:
    return CalibrationVerdict(calibration=None, rejection=reason)


def calibrate_court(
    taps: Sequence[CourtTap],
    frame_size: tuple[int, int],
    *,
    sigma_tap_px: float = DEFAULT_SIGMA_TAP_PX,
    video_frame_size: tuple[int, int] | None = None,
) -> CalibrationVerdict:
    """Validate, fit and gate a tap set on a ``(width, height)`` frame.

    ``video_frame_size`` is the decoded, rotated video frame. When given, an
    aspect mismatch over 2 % means the taps were made on a cropped or
    letterboxed picture, and the calibration is refused.
    """
    if video_frame_size is not None and _frame_shape_mismatch(frame_size, video_frame_size):
        return _reject(CalibrationRejection.FRAME_SHAPE_MISMATCH)
    rejection = validate_taps(taps, frame_size)
    if rejection is not None:
        return _reject(rejection)

    court = np.array([keypoint_xy(t.keypoint) for t in taps], dtype=np.float64)
    image = np.array([(t.u_px, t.v_px) for t in taps], dtype=np.float64)
    h_ci, condition = fit_homography_dlt(court, image)
    if condition < MIN_DLT_CONDITION:
        return _reject(CalibrationRejection.ILL_CONDITIONED)

    errors = reprojection_errors_px(h_ci, court, image)
    rms = float(np.sqrt(np.mean(errors**2)))
    worst = float(np.max(errors))
    to_1280 = _to_1280(frame_size)
    if len(taps) >= REPROJECTION_GATE_MIN_TAPS and (
        rms * to_1280 > MAX_REPROJECTION_RMS_PX or worst * to_1280 > MAX_REPROJECTION_PX
    ):
        return _reject(CalibrationRejection.REPROJECTION_ERROR_HIGH)

    pose = decompose_camera_pose(h_ci, frame_size)
    if pose is None or not _pose_plausible(pose, frame_size):
        return _reject(CalibrationRejection.CAMERA_POSE_IMPLAUSIBLE)

    calls_allowed = len(taps) >= CALLS_MIN_TAPS
    return CalibrationVerdict(
        calibration=CourtCalibration(
            h_court_to_image=h_ci,
            h_image_to_court=invert_homography(h_ci),
            pose=pose,
            frame_size=frame_size,
            taps=tuple(taps),
            reprojection_rms_px=rms,
            reprojection_max_px=worst,
            sigma_tap_px=sigma_tap_px,
            calls_allowed=calls_allowed,
            flags=() if calls_allowed else (UNVERIFIED_CALIBRATION_FLAG,),
            version=CALIBRATION_VERSION,
        ),
        rejection=None,
    )
