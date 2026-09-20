"""Calibration mapping and pose->CAL_SPACE projection (PIPELINE.md 10.1, 11.1).

PURE. numpy and stdlib only. No cv2, no PyAV, no I/O, no clock. This module
turns already-captured facts -- two tapped preview points, a decoded frame
shape, a ``NormalizedSequence`` -- into CAL_SPACE pixel quantities. Acquiring
frames is ``app/ball/frames.py``'s job.

CAL_SPACE (PIPELINE.md 10.1): the decoded frame AFTER display-matrix rotation,
downscaled so the long edge is 1280 px, **y pointing DOWN**. The pose stream's
640 px space is the same space at half the scale, so a landmark crosses with a
single scalar.

THE TRAP THIS MODULE EXISTS TO AVOID
------------------------------------
``segment_px`` is measured in TRUE PIXELS, never in normalized units. On a 9:16
frame one normalized x unit is 0.56 the physical length of one normalized y
unit, so a diagonal segment measured normalized is wrong by a view-dependent
factor -- and the resulting mph is plausible, not absurd. PIPELINE.md 11.1
calls this the single most likely implementation bug in the feature.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

import numpy as np
import numpy.typing as npt

from app.models.enums import BallSpeedUnavailableReason
from app.models.internal import NormalizedSequence
from app.models.requests import BallSpeedCalibration, NormalizedPoint
from app.models.responses import CalibrationEcho

#: 10.1 -- CAL_SPACE long edge.
CAL_SPACE_LONG_EDGE_PX: Final[int] = 1280

#: 11.1 -- decoded post-rotation aspect must match the tapped preview's within
#: this fraction, or the taps were made on a cropped/letterboxed surface.
FRAME_SHAPE_TOLERANCE: Final[float] = 0.02

#: 11.1 -- plausibility band on the derived scale. Outside it, something is in
#: the wrong coordinate space and the honest answer is null, not a number.
MIN_PX_PER_M: Final[float] = 8.0
MAX_PX_PER_M: Final[float] = 300.0

#: 11.1 / speed.py:74 -- a shorter tapped segment cannot carry the scale.
MIN_SEGMENT_PX: Final[float] = 60.0

#: 10.5 -- the ball must be seeded within this distance of the racket wrist.
SEED_GATE_TU: Final[float] = 0.8

#: BlazePose indices of the torso + leg landmarks the exclusion box is built
#: from (``detector.py:280-285``). Arms, racket hand and head are DELIBERATELY
#: absent: the ball sits next to the racket hand at contact and boxing that
#: region out would delete the seed detection.
TORSO_LEG_LANDMARKS: Final[tuple[int, ...]] = (11, 12, 23, 24, 25, 26, 27, 28)

LEFT_WRIST: Final[int] = 15
RIGHT_WRIST: Final[int] = 16

_LEGAL_ROTATIONS: Final[tuple[int, ...]] = (0, 90, 180, 270)


class CalibrationRejected(Exception):
    """Calibration could not be mapped. Carries the reason, never a guess."""

    def __init__(self, reason: BallSpeedUnavailableReason, message: str) -> None:
        self.reason: BallSpeedUnavailableReason = reason
        super().__init__(f"{reason.value}: {message}")


# --------------------------------------------------------------------------- #
# Coordinate-space helpers
# --------------------------------------------------------------------------- #


def cal_space_size(
    width_px: int, height_px: int, long_edge_px: int = CAL_SPACE_LONG_EDGE_PX
) -> tuple[int, int]:
    """Post-rotation frame size scaled so the long edge is ``long_edge_px``.

    Never upscales, matching ``video_io.scaled_size``: a 720 px source stays
    720 px rather than being interpolated into detail it does not have.
    """
    if width_px <= 0 or height_px <= 0:
        raise ValueError("width_px and height_px must be positive")
    longest = max(width_px, height_px)
    if longest <= long_edge_px:
        return int(width_px), int(height_px)
    scale = float(long_edge_px) / float(longest)
    return max(1, round(width_px * scale)), max(1, round(height_px * scale))


def rotate_normalized(x: float, y: float, rotation_deg: int) -> tuple[float, float]:
    """Rotate a point in the unit square CLOCKWISE by ``rotation_deg``.

    Normalized coordinates, y DOWN. A clockwise quarter turn sends a pixel at
    ``(col, row)`` in a W x H image to ``(H - row, col)`` in the H x W image,
    which in normalized terms is ``(1 - y, x)``.
    """
    if rotation_deg not in _LEGAL_ROTATIONS:
        raise ValueError(f"rotation_deg must be one of {_LEGAL_ROTATIONS}")
    if rotation_deg == 0:
        return float(x), float(y)
    if rotation_deg == 90:
        return 1.0 - float(y), float(x)
    if rotation_deg == 180:
        return 1.0 - float(x), 1.0 - float(y)
    return float(y), 1.0 - float(x)


def preview_point_to_cal_px(
    point: NormalizedPoint,
    *,
    capture_rotation_deg: int,
    container_rotation_deg: int,
    cal_width_px: int,
    cal_height_px: int,
) -> tuple[float, float]:
    """One tapped preview point -> CAL_SPACE pixels (PIPELINE.md 11.1).

    The chain, inverted then reapplied::

        preview-normalized
          -> un-rotate by capture_rotation_deg   (the client's preview rotation)
          -> recorded-frame normalized
          -> apply container display-matrix rotation
          -> CAL_SPACE px

    Multiplying by the CAL_SPACE dimensions is the LAST step, deliberately: the
    rotations are exact in normalized space and the pixel conversion is where
    the aspect ratio enters exactly once.
    """
    unrotated = rotate_normalized(point.x, point.y, (360 - capture_rotation_deg) % 360)
    rotated = rotate_normalized(unrotated[0], unrotated[1], container_rotation_deg % 360)
    return rotated[0] * float(cal_width_px), rotated[1] * float(cal_height_px)


def frame_shape_matches(
    capture_width_px: int,
    capture_height_px: int,
    decoded_width_px: int,
    decoded_height_px: int,
    *,
    tolerance: float = FRAME_SHAPE_TOLERANCE,
) -> bool:
    """11.1 self-check: preview aspect vs decoded post-rotation aspect.

    A mismatch means the taps were made on a cropped or letterboxed preview, so
    the mapping above is invalid. Without this check a client presenting a
    centre-cropped preview produces a quietly wrong scale on every clip.
    """
    if min(capture_width_px, capture_height_px, decoded_width_px, decoded_height_px) <= 0:
        return False
    captured = float(capture_width_px) / float(capture_height_px)
    decoded = float(decoded_width_px) / float(decoded_height_px)
    return abs(captured - decoded) <= tolerance * decoded


def scale_from_calibration(segment_px: float, distance_m: float) -> float:
    """``segment_px / distance_m``. Pixels per metre in CAL_SPACE.

    Raises:
        CalibrationRejected: on a segment too short to carry a scale, or a
            resulting scale outside the 11.1 plausibility band.
    """
    if not math.isfinite(segment_px) or segment_px < MIN_SEGMENT_PX:
        raise CalibrationRejected(
            BallSpeedUnavailableReason.CALIBRATION_IMPLAUSIBLE,
            f"tapped segment {segment_px:.1f} px is under {MIN_SEGMENT_PX} px",
        )
    if not math.isfinite(distance_m) or distance_m <= 0.0:
        raise CalibrationRejected(
            BallSpeedUnavailableReason.CALIBRATION_IMPLAUSIBLE,
            "reference distance is not a positive number of metres",
        )
    px_per_m = float(segment_px) / float(distance_m)
    if not MIN_PX_PER_M <= px_per_m <= MAX_PX_PER_M:
        raise CalibrationRejected(
            BallSpeedUnavailableReason.CALIBRATION_IMPLAUSIBLE,
            f"{px_per_m:.2f} px/m is outside [{MIN_PX_PER_M}, {MAX_PX_PER_M}]",
        )
    return px_per_m


def map_calibration_points(
    calibration: BallSpeedCalibration,
    *,
    container_rotation_deg: int,
    cal_width_px: int,
    cal_height_px: int,
) -> CalibrationEcho:
    """PIPELINE.md 11.1, end to end. Raises rather than returning a bad scale.

    Raises:
        CalibrationRejected: ``CALIBRATION_FRAME_MISMATCH`` when the preview
            aspect does not match the decoded frame, ``CALIBRATION_IMPLAUSIBLE``
            when the derived scale is outside the plausibility band.
    """
    if not frame_shape_matches(
        calibration.capture_width_px,
        calibration.capture_height_px,
        cal_width_px,
        cal_height_px,
    ):
        raise CalibrationRejected(
            BallSpeedUnavailableReason.CALIBRATION_FRAME_MISMATCH,
            f"preview {calibration.capture_width_px}x{calibration.capture_height_px} "
            f"does not match decoded {cal_width_px}x{cal_height_px}",
        )

    point_a = preview_point_to_cal_px(
        calibration.point_a,
        capture_rotation_deg=calibration.capture_rotation_deg,
        container_rotation_deg=container_rotation_deg,
        cal_width_px=cal_width_px,
        cal_height_px=cal_height_px,
    )
    point_b = preview_point_to_cal_px(
        calibration.point_b,
        capture_rotation_deg=calibration.capture_rotation_deg,
        container_rotation_deg=container_rotation_deg,
        cal_width_px=cal_width_px,
        cal_height_px=cal_height_px,
    )
    # TRUE PIXELS. Never the normalized separation. See the module docstring.
    segment_px = math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])
    px_per_m = scale_from_calibration(segment_px, calibration.distance_m)

    return CalibrationEcho(
        reference=calibration.reference,
        distance_m=float(calibration.distance_m),
        segment_px=float(segment_px),
        px_per_m=float(px_per_m),
        point_a_cal_px=point_a,
        point_b_cal_px=point_b,
        frame_shape_check_passed=True,
    )


# --------------------------------------------------------------------------- #
# Pose -> CAL_SPACE
# --------------------------------------------------------------------------- #


def pose_to_cal_scale(pose_long_edge_px: int, cal_long_edge_px: int) -> float:
    """The single scalar that maps pose-stream pixels into CAL_SPACE.

    Taken from the LONG EDGES, not per-axis: both spaces preserve aspect from
    the same source frame, so the two axis ratios are equal up to the rounding
    in ``scaled_size``, and using one scalar makes an anisotropic scale bug
    impossible to write.
    """
    if pose_long_edge_px <= 0 or cal_long_edge_px <= 0:
        raise ValueError("long edges must be positive")
    return float(cal_long_edge_px) / float(pose_long_edge_px)


def landmarks_to_cal_px(
    seq: NormalizedSequence,
    frame_index: int,
    landmark_indices: Sequence[int],
    *,
    cal_scale: float,
) -> npt.NDArray[np.float64]:
    """Body-frame landmarks -> CAL_SPACE pixels, y DOWN. Shape ``(K, 2)``.

    ``points`` is body-frame in torso units with y UP and the mid-hip pinned at
    the origin, so the absolute position comes from
    :attr:`NormalizedSequence.origin_px` -- which exists for exactly this::

        pose_px = origin_px[t] + points[t, k] * torso_scale_px * [1, -1]
        cal_px  = pose_px * cal_scale

    The ``[1, -1]`` undoes Stage 7's y-flip: ``points`` is y UP, every pixel
    space here is y DOWN.

    Raises:
        IndexError: if ``frame_index`` is outside the sequence.
    """
    points = np.asarray(seq.points, dtype=np.float64)
    origin = np.asarray(seq.origin_px, dtype=np.float64).reshape(-1, 2)
    total = int(points.shape[0])
    if not 0 <= int(frame_index) < total or origin.shape[0] != total:
        raise IndexError(f"frame_index {frame_index} outside a {total}-frame sequence")

    selected = points[int(frame_index), list(landmark_indices), :]
    flip = np.array([1.0, -1.0], dtype=np.float64)
    pose_px = origin[int(frame_index)] + selected * float(seq.torso_scale_px) * flip
    return np.asarray(pose_px * float(cal_scale), dtype=np.float64)


def racket_wrist_seed_px(
    seq: NormalizedSequence,
    frame_index: int,
    racket_wrist_index: int,
    *,
    cal_scale: float,
) -> tuple[float, float]:
    """``seed_xy`` for ``detect_ball_track``: the racket wrist in CAL_SPACE."""
    point = landmarks_to_cal_px(seq, frame_index, (racket_wrist_index,), cal_scale=cal_scale)
    return float(point[0, 0]), float(point[0, 1])


def seed_gate_px(seq: NormalizedSequence, *, cal_scale: float) -> float:
    """``SEED_GATE_TU`` expressed in CAL_SPACE pixels for this subject."""
    return float(SEED_GATE_TU * float(seq.torso_scale_px) * float(cal_scale))


__all__: Sequence[str] = (
    "CAL_SPACE_LONG_EDGE_PX",
    "FRAME_SHAPE_TOLERANCE",
    "MAX_PX_PER_M",
    "MIN_PX_PER_M",
    "MIN_SEGMENT_PX",
    "SEED_GATE_TU",
    "TORSO_LEG_LANDMARKS",
    "CalibrationRejected",
    "cal_space_size",
    "frame_shape_matches",
    "landmarks_to_cal_px",
    "map_calibration_points",
    "pose_to_cal_scale",
    "preview_point_to_cal_px",
    "racket_wrist_seed_px",
    "rotate_normalized",
    "scale_from_calibration",
    "seed_gate_px",
)
