"""Pure cross-frame ball association: candidates in, a track out (PIPELINE.md 10.5).

Canonical home per PIPELINE.md 3.1: ``app/ball/track.py`` is PURE -- numpy and
stdlib math only, and in particular NO ``cv2``. It never performs I/O, never
decodes video, never touches the network and never reads a clock. It receives
candidates that ``app/ball/detector.py`` already extracted from frames.

The shared value objects (``ContourFeatures``, ``BallCandidate``) and the tier
constants live here rather than in ``detector.py`` precisely so this module can
stay free of ``cv2``: they are plain dataclasses and plain ints with no OpenCV
dependency, and ``detector.py`` imports them from here.

Coordinate space. All pixel inputs and outputs are in ``CAL_SPACE``
(PIPELINE.md 10.1): the decoded frame after display-matrix rotation, downscaled
so the long edge is 1280 px, y pointing DOWN. This module does not rescale
anything.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from app.models.enums import BallSpeedUnavailableReason

# --------------------------------------------------------------------------- #
# Constants -- PIPELINE.md 10.1 - 10.5. No magic numbers below this block.
# --------------------------------------------------------------------------- #

#: 10.2 -- representative CAL_SPACE scale from the worked example (court width
#: filling ~80 % of a 1920 px frame at ~8 m). Used only as the default for the
#: kinematic gate radius when the caller has no calibration scale to hand.
CAL_SPACE_PX_PER_M: Final[float] = 93.0

#: 10.2 -- phone video is overwhelmingly 30 or 60 fps; 30 is the expected case.
DEFAULT_FPS: Final[float] = 30.0

# -- 10.4 detection tiers ---------------------------------------------------- #

TIER_ROUND: Final[int] = 1
TIER_STREAK: Final[int] = 2
TIER_REJECTED: Final[int] = 0

#: Major-axis direction must sit within this of the track last step direction.
#: Applied only once the track has >= 1 step. The primary streak FP killer.
STREAK_ORIENTATION_TOLERANCE_DEG: Final[float] = 25.0

# -- 10.5 association -------------------------------------------------------- #

#: Above any recorded serve (257 km/h).
V_MAX_M_PER_S: Final[float] = 71.5
#: Gate shrink once the track has a velocity estimate.
GATE_SHRINK_FACTOR: Final[float] = 0.35
MIN_GATE_RADIUS_PX: Final[float] = 8.0
#: A >35 deg turn means a bad association or a bounce; both mean stop.
DIRECTION_CHANGE_LIMIT_DEG: Final[float] = 35.0
#: A step under this is a static artifact.
MIN_STEP_PX: Final[float] = 2.0
#: Up to 2 consecutive misses are coasted; a third terminates the track.
MAX_COASTED_FRAMES: Final[int] = 2
#: A partially visible ball has a biased centroid.
EXIT_FRAME_MARGIN_PX: Final[float] = 12.0
#: Fewer accepted detections than this yields no track at all.
MIN_ACCEPTED_DETECTIONS: Final[int] = 3

# -- termination reasons ----------------------------------------------------- #

TERMINATED_WINDOW_END: Final[str] = "window_end"
TERMINATED_DIRECTION_CHANGE: Final[str] = "direction_change"
TERMINATED_COAST_EXHAUSTED: Final[str] = "coast_exhausted"


# --------------------------------------------------------------------------- #
# Value objects -- plain dataclasses, no cv2 handles, no iterators
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContourFeatures:
    """Geometry of one candidate contour, in CAL_SPACE px (PIPELINE.md 10.4)."""

    x: float
    y: float
    area: float
    perimeter: float
    circularity: float
    major_axis_px: float
    minor_axis_px: float
    aspect: float
    solidity: float
    fill: float
    #: Major-axis orientation folded to [0, 180); undirected, y DOWN.
    orientation_deg: float


@dataclass(frozen=True)
class BallCandidate:
    """A contour that passed tier A or tier B in a single frame."""

    frame_index: int
    features: ContourFeatures
    tier: int

    @property
    def x(self) -> float:
        """Centroid x in CAL_SPACE pixels."""
        return self.features.x

    @property
    def y(self) -> float:
        """Centroid y in CAL_SPACE pixels."""
        return self.features.y


@dataclass(frozen=True)
class BallTrack:
    """The ball-side seam object (PIPELINE.md 4.2 / 3.2). Plain numpy + scalars.

    Frozen, ``.npz``-serializable, no handles and no cv2 objects. The temporal
    fields of the 3.2 definition (``timestamps_s``, ``contact_time_s``,
    ``window_start_s``, ``window_end_s``) are NOT populated here: they come from
    the impure frame-acquisition pass, which this module deliberately does not
    perform. ``frame_offsets`` carries the index within the frame stack it was
    given, which is what a frames-in/positions-out module can honestly know.
    """

    #: (N, 2) float32 -- centroids in CAL_SPACE pixels, y DOWN.
    xy_px: npt.NDArray[np.float32]
    #: (N,) int32 -- index within the frame stack passed in.
    frame_offsets: npt.NDArray[np.int32]
    #: (N,) float32 -- depth-drift proxy (PIPELINE.md 10.6).
    minor_axis_px: npt.NDArray[np.float32]
    #: (N,) float32
    major_axis_px: npt.NDArray[np.float32]
    #: (N,) uint8 -- 1 = round, 2 = streak.
    tier: npt.NDArray[np.uint8]
    terminated_by: str
    coasted_frames: int


@dataclass(frozen=True)
class BallDetectionResult:
    """Either a track, or ``None`` plus the reason no track was produced."""

    track: BallTrack | None
    reason: BallSpeedUnavailableReason | None


# --------------------------------------------------------------------------- #
# PURE -- association across frames (PIPELINE.md 10.5). No cv2.
# --------------------------------------------------------------------------- #


def _direction_deg(dx: float, dy: float) -> float:
    """Directed angle of a step, in degrees, in (-180, 180]."""
    return math.degrees(math.atan2(dy, dx))


def direction_difference_deg(first_deg: float, second_deg: float) -> float:
    """Absolute difference between two DIRECTED angles, folded to [0, 180]."""
    return abs((first_deg - second_deg + 180.0) % 360.0 - 180.0)


def axis_difference_deg(axis_deg: float, direction_deg: float) -> float:
    """Difference between an UNDIRECTED axis and a directed angle, in [0, 90]."""
    delta = abs((axis_deg - direction_deg) % 180.0)
    return min(delta, 180.0 - delta)


def _is_near_frame_edge(candidate: BallCandidate, width: int, height: int) -> bool:
    """Exit-frame trim: a partially visible ball has a biased centroid."""
    return (
        candidate.x < EXIT_FRAME_MARGIN_PX
        or candidate.y < EXIT_FRAME_MARGIN_PX
        or candidate.x > width - 1 - EXIT_FRAME_MARGIN_PX
        or candidate.y > height - 1 - EXIT_FRAME_MARGIN_PX
    )


def _select_seed(
    candidates: Sequence[BallCandidate],
    seed_xy: tuple[float, float] | None,
    seed_gate_px: float | None,
) -> BallCandidate | None:
    """Seed the track in the first frame (PIPELINE.md 10.5 step 1).

    With ``seed_xy`` (the racket-hand wrist mapped into CAL_SPACE) the nearest
    candidate wins and is rejected outright beyond ``seed_gate_px``. Without a
    wrist position -- this module does not consume pose data -- the largest
    candidate wins, which is deterministic and prefers the most ball-like blob.
    """
    if not candidates:
        return None
    if seed_xy is None:
        return max(candidates, key=lambda c: c.features.area)
    seed_x, seed_y = seed_xy
    nearest = min(candidates, key=lambda c: math.hypot(c.x - seed_x, c.y - seed_y))
    if (
        seed_gate_px is not None
        and math.hypot(nearest.x - seed_x, nearest.y - seed_y) > seed_gate_px
    ):
        return None
    return nearest


def _build_track(
    accepted: Sequence[BallCandidate], terminated_by: str, coasted_frames: int
) -> BallTrack:
    """Assemble the frozen seam object from the accepted detections."""
    return BallTrack(
        xy_px=np.array([[c.x, c.y] for c in accepted], dtype=np.float32).reshape(-1, 2),
        frame_offsets=np.array([c.frame_index for c in accepted], dtype=np.int32),
        minor_axis_px=np.array(
            [c.features.minor_axis_px for c in accepted], dtype=np.float32
        ),
        major_axis_px=np.array(
            [c.features.major_axis_px for c in accepted], dtype=np.float32
        ),
        tier=np.array([c.tier for c in accepted], dtype=np.uint8),
        terminated_by=terminated_by,
        coasted_frames=coasted_frames,
    )


def associate_candidates(
    candidates_by_frame: Sequence[Sequence[BallCandidate]],
    frame_shape: tuple[int, int],
    *,
    fps: float = DEFAULT_FPS,
    px_per_m: float = CAL_SPACE_PX_PER_M,
    seed_xy: tuple[float, float] | None = None,
    seed_gate_px: float | None = None,
) -> BallDetectionResult:
    """Greedy nearest-neighbour association with the kinematic and direction
    gates of PIPELINE.md 10.5.

    ``frame_shape`` is ``(height, width)`` of the CAL_SPACE frames, needed only
    for the exit-frame trim. Precision is enforced here, not at the contour
    layer: a spurious candidate must sit inside the kinematic gate AND agree in
    direction with the previous step to within 35 degrees.
    """
    if fps <= 0.0 or px_per_m <= 0.0:
        raise ValueError("fps and px_per_m must be positive")

    height, width = frame_shape
    trimmed: list[list[BallCandidate]] = [
        [c for c in frame_candidates if not _is_near_frame_edge(c, width, height)]
        for frame_candidates in candidates_by_frame
    ]
    if not trimmed:
        return BallDetectionResult(None, BallSpeedUnavailableReason.NO_TRACK_SEEDED)

    seed = _select_seed(trimmed[0], seed_xy, seed_gate_px)
    if seed is None:
        return BallDetectionResult(None, BallSpeedUnavailableReason.NO_TRACK_SEEDED)

    r_max_per_frame = V_MAX_M_PER_S * (1.0 / fps) * px_per_m

    accepted: list[BallCandidate] = [seed]
    last_x, last_y = seed.x, seed.y
    last_frame_index = seed.frame_index
    velocity: tuple[float, float] | None = None
    last_direction_deg: float | None = None
    coasted_frames = 0
    consecutive_misses = 0
    terminated_by = TERMINATED_WINDOW_END

    for frame_index in range(1, len(trimmed)):
        gap = frame_index - last_frame_index
        r_max = r_max_per_frame * gap
        if velocity is None:
            predicted_x, predicted_y = last_x, last_y
            gate = r_max
        else:
            predicted_x = last_x + velocity[0] * gap
            predicted_y = last_y + velocity[1] * gap
            gate = max(MIN_GATE_RADIUS_PX, GATE_SHRINK_FACTOR * r_max)

        best: BallCandidate | None = None
        best_distance = math.inf
        for candidate in trimmed[frame_index]:
            distance = math.hypot(candidate.x - predicted_x, candidate.y - predicted_y)
            if distance > gate:
                continue
            if math.hypot(candidate.x - last_x, candidate.y - last_y) < MIN_STEP_PX:
                continue
            if candidate.tier == TIER_STREAK and last_direction_deg is not None:
                agreement = axis_difference_deg(
                    candidate.features.orientation_deg, last_direction_deg
                )
                if agreement > STREAK_ORIENTATION_TOLERANCE_DEG:
                    continue
            if distance < best_distance:
                best, best_distance = candidate, distance

        if best is None:
            consecutive_misses += 1
            if consecutive_misses > MAX_COASTED_FRAMES:
                terminated_by = TERMINATED_COAST_EXHAUSTED
                break
            coasted_frames += 1
            continue

        step_direction = _direction_deg(best.x - last_x, best.y - last_y)
        if (
            last_direction_deg is not None
            and direction_difference_deg(step_direction, last_direction_deg)
            > DIRECTION_CHANGE_LIMIT_DEG
        ):
            terminated_by = TERMINATED_DIRECTION_CHANGE
            break

        velocity = ((best.x - last_x) / gap, (best.y - last_y) / gap)
        last_direction_deg = step_direction
        last_x, last_y = best.x, best.y
        last_frame_index = frame_index
        consecutive_misses = 0
        accepted.append(best)

    if len(accepted) < MIN_ACCEPTED_DETECTIONS:
        return BallDetectionResult(None, BallSpeedUnavailableReason.TOO_FEW_DETECTIONS)
    return BallDetectionResult(
        _build_track(accepted, terminated_by, coasted_frames), None
    )


__all__: Sequence[str] = (
    "CAL_SPACE_PX_PER_M",
    "DEFAULT_FPS",
    "DIRECTION_CHANGE_LIMIT_DEG",
    "EXIT_FRAME_MARGIN_PX",
    "GATE_SHRINK_FACTOR",
    "MAX_COASTED_FRAMES",
    "MIN_ACCEPTED_DETECTIONS",
    "MIN_GATE_RADIUS_PX",
    "MIN_STEP_PX",
    "STREAK_ORIENTATION_TOLERANCE_DEG",
    "TERMINATED_COAST_EXHAUSTED",
    "TERMINATED_DIRECTION_CHANGE",
    "TERMINATED_WINDOW_END",
    "TIER_REJECTED",
    "TIER_ROUND",
    "TIER_STREAK",
    "V_MAX_M_PER_S",
    "BallCandidate",
    "BallDetectionResult",
    "BallTrack",
    "ContourFeatures",
    "associate_candidates",
    "axis_difference_deg",
    "direction_difference_deg",
)
