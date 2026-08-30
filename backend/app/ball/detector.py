"""Pure-CV ball detection: frames in, ball positions out (PIPELINE.md Stage 10).

Canonical home per PIPELINE.md 3.1: ``app/ball/detector.py`` is PURE-CV --
background median, motion + colour masking and contour tiering. The cross-frame
association half lives next door in ``app/ball/track.py`` (pure numpy), from
which this module imports the shared value objects and tier constants so that
``track.py`` never has to import ``cv2``.

Scope and purity. This module never performs I/O. It does not decode video,
open files, touch the network, or read a clock. Frame *acquisition* -- the
second decode pass described in PIPELINE.md 10.2/10.3 -- is out of scope and
belongs to the impure ``ball/frames.py``; this module receives frames it did
not open. ``cv2`` is used strictly as an image-processing library operating on
arrays passed in, never as a demuxer. Every function here is deterministic:
the same frames in produce the same track out.

Coordinate space. All pixel inputs and outputs are in ``CAL_SPACE``
(PIPELINE.md 10.1): the decoded frame after display-matrix rotation,
downscaled so the long edge is 1280 px, y pointing DOWN. This module does not
rescale anything -- it assumes the caller already handed it CAL_SPACE frames.

Not implemented here (deliberately): Stage 11 speed calculation, calibration
mapping, the orchestrator, and any Pydantic response model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from app.ball.track import (
    CAL_SPACE_PX_PER_M,
    DEFAULT_FPS,
    TIER_REJECTED,
    TIER_ROUND,
    TIER_STREAK,
    BallCandidate,
    BallDetectionResult,
    ContourFeatures,
    associate_candidates,
)
from app.models.enums import BallSpeedUnavailableReason

# --------------------------------------------------------------------------- #
# Constants -- PIPELINE.md 10.1 - 10.4. No magic numbers below this block.
# --------------------------------------------------------------------------- #

#: 10.1 -- CAL_SPACE is the frame downscaled to this long edge.
CAL_SPACE_LONG_EDGE_PX: Final[int] = 1280

# -- 10.3 background model and motion mask ---------------------------------- #

#: Frames sampled evenly from the pre-roll for the fixed temporal median.
BACKGROUND_SAMPLE_FRAMES: Final[int] = 15

#: ``absdiff(frame_gray, background_gray) > 18`` in 8-bit levels.
MOTION_DIFF_THRESHOLD: Final[int] = 18

#: MORPH_OPEN removes isolated speckle; MORPH_CLOSE reconnects a broken streak.
#: Order matters -- closing first would fuse speckle into fake blobs.
MORPH_OPEN_KERNEL_SIZE: Final[tuple[int, int]] = (3, 3)
MORPH_CLOSE_KERNEL_SIZE: Final[tuple[int, int]] = (5, 5)

# -- 10.4 colour gate -------------------------------------------------------- #

#: Optic-yellow branch (OpenCV HSV, H in [0, 179]).
YELLOW_HSV_LOWER: Final[tuple[int, int, int]] = (25, 60, 120)
YELLOW_HSV_UPPER: Final[tuple[int, int, int]] = (45, 255, 255)

#: Achromatic-bright branch: V > 200 and S < 60. Dangerous alone (it matches
#: every white court line and every white shoe) and therefore only ever
#: evaluated inside the motion mask, which static lines fail categorically.
ACHROMATIC_HSV_LOWER: Final[tuple[int, int, int]] = (0, 0, 201)
ACHROMATIC_HSV_UPPER: Final[tuple[int, int, int]] = (179, 59, 255)

# -- 10.4 tier A: round (low blur, short exposure, or slow ball) ------------- #

TIER_A_AREA_MIN_PX2: Final[float] = 12.0
TIER_A_AREA_MAX_PX2: Final[float] = 1200.0
TIER_A_CIRCULARITY_MIN: Final[float] = 0.65
TIER_A_ASPECT_MAX: Final[float] = 1.6
TIER_A_SOLIDITY_MIN: Final[float] = 0.85
TIER_A_FILL_MIN: Final[float] = 0.55

# -- 10.4 tier B: streak (the motion-blur case, and the common one) ---------- #

TIER_B_AREA_MIN_PX2: Final[float] = 12.0
TIER_B_AREA_MAX_PX2: Final[float] = 1500.0
#: Loosened deliberately: a 6x47 capsule sits at ~0.24, so a circularity bound
#: tuned for a round ball would reject essentially every in-flight detection.
TIER_B_CIRCULARITY_MIN: Final[float] = 0.20
TIER_B_ASPECT_MIN: Final[float] = 1.6
TIER_B_ASPECT_MAX: Final[float] = 12.0
#: The ball thickness does not blur: the strongest single constraint, and
#: nearly invariant to shutter speed.
TIER_B_MINOR_AXIS_MIN_PX: Final[float] = 3.0
TIER_B_MINOR_AXIS_MAX_PX: Final[float] = 20.0
TIER_B_SOLIDITY_MIN: Final[float] = 0.80
TIER_B_FILL_MIN: Final[float] = 0.50

# -- 10.4 player exclusion --------------------------------------------------- #

#: The torso+legs bounding box is dilated 15 %. Arms, racket hand and head are
#: NOT excluded: the ball is adjacent to the racket hand at contact and
#: excluding that region would delete the seed detection.
PLAYER_BOX_DILATION: Final[float] = 0.15


# --------------------------------------------------------------------------- #
# PURE-CV -- background model and masking (PIPELINE.md 10.3)
# --------------------------------------------------------------------------- #


def to_grayscale(frames: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Convert a (T, H, W, 3) BGR stack to a (T, H, W) grayscale stack."""
    return np.stack(
        [cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames], axis=0
    )


def sample_background_frames(
    frames: npt.NDArray[np.uint8], count: int = BACKGROUND_SAMPLE_FRAMES
) -> npt.NDArray[np.uint8]:
    """Sample ``count`` frames evenly from ``frames`` (PIPELINE.md 10.3)."""
    total = int(frames.shape[0])
    if total <= count:
        return frames
    indices = np.unique(np.linspace(0, total - 1, count).round().astype(np.int64))
    return frames[indices]


def build_background(frames: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Fixed temporal median background, in grayscale (PIPELINE.md 10.3).

    A pure reduce over a stacked array -- no OpenCV state machine, no
    learning-rate tuning, no warm-up semantics. A small fast object is excluded
    from a per-pixel median by construction. Computed once and held fixed across
    the whole measurement window.
    """
    if frames.ndim != 4 or frames.shape[0] == 0:
        raise ValueError("background frames must be a non-empty (T, H, W, 3) stack")
    gray = to_grayscale(sample_background_frames(frames))
    return np.median(gray, axis=0).astype(np.uint8)


def motion_mask(
    frame_bgr: npt.NDArray[np.uint8], background_gray: npt.NDArray[np.uint8]
) -> npt.NDArray[np.uint8]:
    """absdiff > 18, then MORPH_OPEN 3x3 then MORPH_CLOSE 5x5 (PIPELINE.md 10.3)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray, background_gray)
    mask = (diff > MOTION_DIFF_THRESHOLD).astype(np.uint8) * 255
    open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, MORPH_OPEN_KERNEL_SIZE)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, MORPH_CLOSE_KERNEL_SIZE)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)


def colour_mask(frame_bgr: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Optic-yellow OR achromatic-bright HSV gate (PIPELINE.md 10.4)."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(
        hsv,
        np.array(YELLOW_HSV_LOWER, dtype=np.uint8),
        np.array(YELLOW_HSV_UPPER, dtype=np.uint8),
    )
    achromatic = cv2.inRange(
        hsv,
        np.array(ACHROMATIC_HSV_LOWER, dtype=np.uint8),
        np.array(ACHROMATIC_HSV_UPPER, dtype=np.uint8),
    )
    return cv2.bitwise_or(yellow, achromatic)


def candidate_mask(
    frame_bgr: npt.NDArray[np.uint8], background_gray: npt.NDArray[np.uint8]
) -> npt.NDArray[np.uint8]:
    """Colour gate ANDed with the motion mask.

    Motion is applied first and is not optional: the achromatic branch is only
    safe inside it (PIPELINE.md 10.4).
    """
    return cv2.bitwise_and(
        motion_mask(frame_bgr, background_gray), colour_mask(frame_bgr)
    )


# --------------------------------------------------------------------------- #
# PURE-CV -- contour features and the two-tier filter (PIPELINE.md 10.4)
# --------------------------------------------------------------------------- #


def _normalize_axis_deg(angle_deg: float) -> float:
    """Fold an undirected axis angle into [0, 180)."""
    return angle_deg % 180.0


def contour_features(contour: npt.NDArray[np.int32]) -> ContourFeatures | None:
    """Reduce one contour to the feature set of PIPELINE.md 10.4.

    Returns ``None`` for a degenerate contour (zero area, zero perimeter or a
    zero-width min-area rect), which cannot be scored and is never a ball.
    """
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    if area <= 0.0 or perimeter <= 0.0:
        return None

    (rect_x, rect_y), (width, height), rect_angle = cv2.minAreaRect(contour)
    major = float(max(width, height))
    minor = float(min(width, height))
    if minor <= 0.0 or major <= 0.0:
        return None

    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    if hull_area <= 0.0:
        return None

    moments = cv2.moments(contour)
    if moments["m00"] > 0.0:
        centroid_x = float(moments["m10"] / moments["m00"])
        centroid_y = float(moments["m01"] / moments["m00"])
    else:
        centroid_x, centroid_y = float(rect_x), float(rect_y)

    orientation = float(rect_angle) if width >= height else float(rect_angle) + 90.0

    return ContourFeatures(
        x=centroid_x,
        y=centroid_y,
        area=area,
        perimeter=perimeter,
        circularity=float(4.0 * math.pi * area / (perimeter * perimeter)),
        major_axis_px=major,
        minor_axis_px=minor,
        aspect=major / minor,
        solidity=area / hull_area,
        fill=area / (major * minor),
        orientation_deg=_normalize_axis_deg(orientation),
    )


def classify_contour(features: ContourFeatures) -> int:
    """Return ``TIER_ROUND``, ``TIER_STREAK`` or ``TIER_REJECTED``.

    The tier-B orientation-agreement criterion is NOT evaluated here: it needs
    the track last step direction and is therefore applied at the association
    layer, once the track has >= 1 step (PIPELINE.md 10.4).
    """
    if (
        TIER_A_AREA_MIN_PX2 <= features.area <= TIER_A_AREA_MAX_PX2
        and features.circularity >= TIER_A_CIRCULARITY_MIN
        and features.aspect <= TIER_A_ASPECT_MAX
        and features.solidity >= TIER_A_SOLIDITY_MIN
        and features.fill >= TIER_A_FILL_MIN
    ):
        return TIER_ROUND
    if (
        TIER_B_AREA_MIN_PX2 <= features.area <= TIER_B_AREA_MAX_PX2
        and features.circularity >= TIER_B_CIRCULARITY_MIN
        and TIER_B_ASPECT_MIN <= features.aspect <= TIER_B_ASPECT_MAX
        and TIER_B_MINOR_AXIS_MIN_PX
        <= features.minor_axis_px
        <= TIER_B_MINOR_AXIS_MAX_PX
        and features.solidity >= TIER_B_SOLIDITY_MIN
        and features.fill >= TIER_B_FILL_MIN
    ):
        return TIER_STREAK
    return TIER_REJECTED


def torso_leg_exclusion_box(
    points_xy: npt.NDArray[np.floating], dilation: float = PLAYER_BOX_DILATION
) -> tuple[float, float, float, float]:
    """Dilated bounding box of TORSO AND LEG landmarks only (PIPELINE.md 10.4).

    ``points_xy`` is a (K, 2) array of shoulder, hip, knee and ankle positions
    ALREADY in CAL_SPACE. Arms, racket hand and head must not be included by the
    caller: the ball sits next to the racket hand at contact, and excluding that
    region would delete the seed detection.
    """
    if points_xy.ndim != 2 or points_xy.shape[0] == 0 or points_xy.shape[1] != 2:
        raise ValueError("points_xy must be a non-empty (K, 2) array")
    x_min = float(np.min(points_xy[:, 0]))
    x_max = float(np.max(points_xy[:, 0]))
    y_min = float(np.min(points_xy[:, 1]))
    y_max = float(np.max(points_xy[:, 1]))
    pad_x = (x_max - x_min) * dilation / 2.0
    pad_y = (y_max - y_min) * dilation / 2.0
    return (x_min - pad_x, y_min - pad_y, x_max + pad_x, y_max + pad_y)


def _is_inside_box(x: float, y: float, box: tuple[float, float, float, float]) -> bool:
    """True when a centroid falls inside a player exclusion box."""
    x_min, y_min, x_max, y_max = box
    return x_min <= x <= x_max and y_min <= y <= y_max


def find_candidates(
    frame_bgr: npt.NDArray[np.uint8],
    background_gray: npt.NDArray[np.uint8],
    frame_index: int,
    exclusion_box: tuple[float, float, float, float] | None = None,
) -> list[BallCandidate]:
    """Every contour in one frame that passes tier A or tier B."""
    mask = candidate_mask(frame_bgr, background_gray)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[BallCandidate] = []
    for contour in contours:
        features = contour_features(contour)
        if features is None:
            continue
        if exclusion_box is not None and _is_inside_box(
            features.x, features.y, exclusion_box
        ):
            continue
        tier = classify_contour(features)
        if tier == TIER_REJECTED:
            continue
        candidates.append(
            BallCandidate(frame_index=frame_index, features=features, tier=tier)
        )
    return candidates


# --------------------------------------------------------------------------- #
# PURE -- entry point
# --------------------------------------------------------------------------- #


def detect_ball_track(
    frames: npt.NDArray[np.uint8],
    *,
    background_frames: npt.NDArray[np.uint8] | None = None,
    exclusion_boxes: Sequence[tuple[float, float, float, float] | None] | None = None,
    fps: float = DEFAULT_FPS,
    px_per_m: float = CAL_SPACE_PX_PER_M,
    seed_xy: tuple[float, float] | None = None,
    seed_gate_px: float | None = None,
) -> BallDetectionResult:
    """Detect and track the ball across a stack of CAL_SPACE frames.

    :param frames: (T, H, W, 3) BGR uint8 measurement-window frames, already
        decoded and already in CAL_SPACE. This module does not open them.
    :param background_frames: optional (T2, H, W, 3) pre-roll stack for the
        fixed temporal median (PIPELINE.md 10.3). When omitted the median is
        taken over ``frames`` themselves, which is legitimate for the same
        reason the pre-roll median is: a small fast object is excluded from a
        per-pixel median by construction.
    :param exclusion_boxes: optional per-frame ``(x_min, y_min, x_max, y_max)``
        torso+leg boxes in CAL_SPACE, one entry per frame; a ``None`` entry
        means no box for that frame.
    :param fps: native frame rate of ``frames``; used only for the gate radius.
    :param px_per_m: CAL_SPACE scale; used only for the gate radius.
    :param seed_xy: racket-hand wrist in CAL_SPACE, if the caller has it.
    :param seed_gate_px: maximum seed distance from ``seed_xy`` (0.8 TU).
    :returns: a ``BallDetectionResult``: a track, or ``None`` plus a reason.
    """
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError("frames must be a (T, H, W, 3) BGR stack")
    if frames.dtype != np.uint8:
        raise ValueError("frames must be uint8")
    if frames.shape[0] == 0:
        return BallDetectionResult(None, BallSpeedUnavailableReason.NO_TRACK_SEEDED)
    if exclusion_boxes is not None and len(exclusion_boxes) != int(frames.shape[0]):
        raise ValueError("exclusion_boxes must have one entry per frame")

    background_gray = build_background(
        frames if background_frames is None else background_frames
    )
    if background_gray.shape != frames.shape[1:3]:
        raise ValueError("background frames must match the shape of frames")

    candidates_by_frame: list[list[BallCandidate]] = [
        find_candidates(
            frames[index],
            background_gray,
            index,
            None if exclusion_boxes is None else exclusion_boxes[index],
        )
        for index in range(int(frames.shape[0]))
    ]

    return associate_candidates(
        candidates_by_frame,
        (int(frames.shape[1]), int(frames.shape[2])),
        fps=fps,
        px_per_m=px_per_m,
        seed_xy=seed_xy,
        seed_gate_px=seed_gate_px,
    )


__all__: Sequence[str] = (
    "BACKGROUND_SAMPLE_FRAMES",
    "CAL_SPACE_LONG_EDGE_PX",
    "MOTION_DIFF_THRESHOLD",
    "PLAYER_BOX_DILATION",
    "build_background",
    "candidate_mask",
    "classify_contour",
    "colour_mask",
    "contour_features",
    "detect_ball_track",
    "find_candidates",
    "motion_mask",
    "sample_background_frames",
    "to_grayscale",
    "torso_leg_exclusion_box",
)
