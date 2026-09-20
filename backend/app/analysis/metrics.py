"""Stage 13: swing metric computation (PURE, PIPELINE.md 4.4 row 13).

Arrays and value objects in, numbers out. No I/O, no MediaPipe, no network.
Nothing in this module raises: every helper returns ``None`` where it cannot
measure, and :func:`compute_swing_metrics` wraps the whole computation.

AXIS CONVENTION -- read before touching any angle here
------------------------------------------------------
Raw MediaPipe y grows DOWNWARD. ``NormalizedSequence.points`` is NOT raw:
Stage 7 step 4 (``normalize.flip_y``) already negated y, so in this module
**y is positive UP and the origin is the mid-hip** (see
``app.models.internal.NormalizedSequence``). Correcting for a downward y here
would double-flip and invert every angle metric. Consequences:

* A pure upward hand path has INCREASING y, so
  ``degrees(atan2(dy, dx))`` with NO negation gives +90 deg -- which is the
  spec's "Positive = low-to-high" for ``swing_path_angle_deg``.
* ``contact_height_ratio`` is ``wrist_y / mid_shoulder_y``: mid-hip y is 0 by
  construction (it is the origin) and mid-shoulder y is POSITIVE. Not inverted.
* ``follow_through_height_tu`` takes the MAXIMUM y after contact, not the
  minimum.

MISSING IS ``None``
-------------------
"A missing metric is ``None``, never a default, never zero" (Stage 13). ``0.0``
is a legitimate measured value for ``contact_point_forward_tu``,
``swing_path_angle_deg``, ``head_stillness_tu`` and others, so an unavailable
metric must never collapse onto it.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.handedness import HINT_CONFIDENCE_FLOOR, racket_wrist_index
from app.analysis.normalize import (
    LEFT_ANKLE,
    LEFT_ELBOW,
    LEFT_HIP,
    LEFT_KNEE,
    LEFT_SHOULDER,
    LEFT_WRIST,
    NOSE,
    RIGHT_ANKLE,
    RIGHT_ELBOW,
    RIGHT_HIP,
    RIGHT_KNEE,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    VISIBILITY_THRESHOLD,
    landmark_visible,
)
from app.models.enums import Handedness, HandednessSource, SwingPhaseName
from app.models.responses import (
    ContactDetection,
    HandednessResult,
    SwingMetrics,
    SwingPhases,
)
from app.models.internal import NormalizedSequence

# Stage 13 table: swing_path_angle_deg is fitted over [contact-6, contact+3].
SWING_PATH_FRAMES_BEFORE: Final[int] = 6
SWING_PATH_FRAMES_AFTER: Final[int] = 3
# wrist_lag_deg is measured at contact - 3 frames.
WRIST_LAG_LEAD_FRAMES: Final[int] = 3
# A line fit through two points has zero residual by construction, so the
# straightness metric would read a perfect 0.0 from no evidence at all.
MIN_FIT_POINTS: Final[int] = 3
# Below this a vector has no meaningful direction: an angle computed from it is
# noise, and noise dressed as a number is worse than None.
MIN_VECTOR_NORM: Final[float] = 1e-9

# Each metric's `view_sensitive` flag, from the Stage 13 table's third column.
# It is not a field of SwingMetrics -- the schema at PIPELINE.md line 1809 puts
# `view_sensitive: bool` on Stage 15's MetricScore -- so Stage 13 exports it as
# this mapping for Stage 15 to attach per metric.
#
# The table has THREE values where the schema has two. "Partial" is resolved to
# True: all three Partial metrics are signed x-displacements, which shift
# materially with camera yaw, and §5's fallback (null out view_sensitive
# metrics) is meant to catch exactly those. `swing_path_angle_deg` is listed as
# "No (sign), Yes (magnitude)" and is resolved to False, because §5 explicitly
# names "the *sign* of swing path angle" among the view-stable quantities the
# metric set was chosen for, and Stage 14's slice rule reads that sign.
# `takeback_displacement_tu` is absent: the Stage 13 table never defines it.
METRIC_VIEW_SENSITIVE: Final[dict[str, bool]] = {
    "shoulder_hip_separation_deg": False,
    "shoulder_turn_deg": True,
    "hip_rotation_deg": True,
    "elbow_angle_at_contact_deg": True,
    "wrist_lag_deg": True,
    "contact_height_ratio": False,
    "contact_point_forward_tu": True,
    "peak_hand_speed_tu_s": True,
    "swing_path_angle_deg": False,
    "swing_plane_deviation_tu": True,
    "knee_flexion_min_deg": True,
    "weight_transfer_tu": True,
    "follow_through_height_tu": False,
    "balance_sway_tu": False,
    "head_stillness_tu": False,
    "tempo_ratio": False,
    "wrist_separation_at_contact_tu": False,
}

# Stage 7 step 5 translates every frame so that the mid-hip sits at the origin.
# Mid-hip x and mid-hip position are therefore IDENTICALLY ZERO in
# NormalizedSequence.points, and so is their velocity. The two metrics defined
# as displacements OF the mid-hip cannot be recovered from this input at all --
# the body frame removes precisely the signal they measure. Returning 0.0 would
# report "no weight transfer, perfect balance" for every clip ever analysed, so
# they return None until Stage 7 carries a pre-translation mid-hip trajectory.
MID_HIP_IS_THE_ORIGIN: Final[str] = (
    "mid-hip displacement is unrecoverable: Stage 7 pins the mid-hip at the origin"
)

# Prefix of the warning appended when compute_swing_metrics takes its exception
# path. Exported so callers can branch on an internal failure rather than
# string-matching a message body.
INTERNAL_FAILURE_WARNING: Final[str] = "swing metric computation failed internally"


# --- geometry helpers (pure, each independently testable) --------------------


def vector_angle_deg(vector: np.ndarray) -> float | None:
    """Direction of a 2-vector in degrees, CCW from +x, in (-180, 180].

    y is positive UP here, so a straight-up vector returns +90 and a
    straight-down vector returns -90. No negation.
    """
    data = np.asarray(vector, dtype=np.float64).reshape(-1)
    if data.shape[0] != 2 or not np.all(np.isfinite(data)):
        return None
    if float(np.hypot(data[0], data[1])) < MIN_VECTOR_NORM:
        return None
    return float(np.degrees(np.arctan2(data[1], data[0])))


def line_angle_deg(start: np.ndarray, end: np.ndarray) -> float | None:
    """Angle of the segment ``start -> end`` relative to the +x axis."""
    return vector_angle_deg(np.asarray(end, dtype=np.float64) - np.asarray(start, dtype=np.float64))


def wrap_degrees(angle: float) -> float:
    """Wrap an angle into (-180, 180]."""
    wrapped = float((float(angle) + 180.0) % 360.0) - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


def signed_angle_difference_deg(first: float, second: float) -> float:
    """``first - second`` wrapped into (-180, 180]."""
    return wrap_degrees(float(first) - float(second))


def joint_angle_deg(
    proximal: np.ndarray, joint: np.ndarray, distal: np.ndarray
) -> float | None:
    """Interior angle at ``joint``, in [0, 180]. None if either limb is degenerate."""
    first = np.asarray(proximal, dtype=np.float64) - np.asarray(joint, dtype=np.float64)
    second = np.asarray(distal, dtype=np.float64) - np.asarray(joint, dtype=np.float64)
    first_norm = float(np.linalg.norm(first))
    second_norm = float(np.linalg.norm(second))
    if first_norm < MIN_VECTOR_NORM or second_norm < MIN_VECTOR_NORM:
        return None
    cosine = float(np.dot(first, second) / (first_norm * second_norm))
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def angular_range_deg(angles: np.ndarray) -> float | None:
    """max - min of a sequence of angles in degrees, unwrapped first.

    Unwrapping matters: a shoulder line crossing the +/-180 boundary otherwise
    reports a ~360 deg turn from a few degrees of real rotation.
    """
    data = np.asarray(angles, dtype=np.float64).reshape(-1)
    if data.shape[0] == 0 or not np.all(np.isfinite(data)):
        return None
    unwrapped = np.degrees(np.unwrap(np.radians(data)))
    return float(np.max(unwrapped) - np.min(unwrapped))


def max_absolute_signed(values: np.ndarray) -> float | None:
    """The element with the largest magnitude, SIGN PRESERVED. None if empty."""
    data = np.asarray(values, dtype=np.float64).reshape(-1)
    if data.shape[0] == 0 or not np.all(np.isfinite(data)):
        return None
    return float(data[int(np.argmax(np.abs(data)))])


def fit_trajectory_line(points: np.ndarray) -> tuple[float, float] | None:
    """Total-least-squares line through a (N, 2) trajectory.

    Returns ``(angle_deg, rms_residual)``. The angle is the direction of TRAVEL
    along the fitted line -- the principal axis is oriented by the net
    displacement -- so a rising path gives a positive angle and a falling path a
    negative one. The residual is the RMS PERPENDICULAR distance to the line.

    Orthogonal regression rather than y-on-x: a near-vertical swing path has an
    unbounded y-on-x slope, and vertical is exactly the case the sign convention
    has to get right.
    """
    data = np.asarray(points, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != 2 or data.shape[0] < MIN_FIT_POINTS:
        return None
    if not np.all(np.isfinite(data)):
        return None
    centred = data - data.mean(axis=0)
    spread = float(np.max(np.linalg.norm(centred, axis=1)))
    if spread < MIN_VECTOR_NORM:
        return None
    _, _, right = np.linalg.svd(centred, full_matrices=False)
    direction = np.asarray(right[0], dtype=np.float64)
    travel = data[-1] - data[0]
    if float(np.dot(direction, travel)) < 0.0:
        direction = -direction
    angle = vector_angle_deg(direction)
    if angle is None:
        return None
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    residuals = centred @ normal
    return angle, float(np.sqrt(np.mean(np.square(residuals))))


def position_spread_tu(points: np.ndarray) -> float | None:
    """Std-dev of a (N, 2) point cloud, as RMS distance from its own mean.

    A 2D "std-dev" is not scalar; this collapses it the usual way,
    ``sqrt(var_x + var_y)``, which is the RMS displacement from the mean
    position and keeps the unit in TU.
    """
    data = np.asarray(points, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != 2 or data.shape[0] < 2:
        return None
    if not np.all(np.isfinite(data)):
        return None
    return float(np.sqrt(float(np.var(data[:, 0])) + float(np.var(data[:, 1]))))


# --- sequence / phase access -------------------------------------------------


def visible_mask(
    seq: NormalizedSequence,
    landmarks: tuple[int, ...],
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> np.ndarray:
    """(T,) bool: every landmark in ``landmarks`` individually visible that frame.

    A malformed or absent visibility array is treated as "no information", not
    as "invisible" -- Stage 7 already gated the frames, and a synthetic sequence
    without visibilities must still be measurable.
    """
    count = int(np.asarray(seq.points, dtype=np.float64).shape[0])
    visibility = np.asarray(seq.visibility, dtype=np.float64)
    if visibility.ndim != 2 or visibility.shape[0] != count or count == 0:
        return np.ones((max(count, 0),), dtype=bool)
    mask = np.ones((count,), dtype=bool)
    for index in landmarks:
        if 0 <= int(index) < visibility.shape[1]:
            mask &= landmark_visible(visibility[:, int(index)], threshold=threshold)
    return np.asarray(mask, dtype=bool)


def frame_is_visible(
    seq: NormalizedSequence,
    frame_index: int,
    landmarks: tuple[int, ...],
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> bool:
    """Whether every landmark in ``landmarks`` was visible at exactly one frame."""
    mask = visible_mask(seq, landmarks, threshold=threshold)
    if mask.shape[0] == 0 or not 0 <= int(frame_index) < mask.shape[0]:
        return False
    return bool(mask[int(frame_index)])


def frame_window(seq: NormalizedSequence, first: int, last: int) -> np.ndarray:
    """Frame indices in the inclusive range ``[first, last]``, clipped to the clip."""
    count = int(np.asarray(seq.points, dtype=np.float64).shape[0])
    if count == 0:
        return np.zeros((0,), dtype=np.int64)
    start = max(int(first), 0)
    end = min(int(last), count - 1)
    if end < start:
        return np.zeros((0,), dtype=np.int64)
    return np.arange(start, end + 1, dtype=np.int64)


def phase_span(phases: SwingPhases | None, name: SwingPhaseName) -> tuple[int, int] | None:
    """Inclusive ``(start_frame, end_frame)`` of one phase, or None if unusable.

    A Stage 12 phase that collapsed to zero duration, or one that is absent, is
    not a window this module can measure over.
    """
    if phases is None:
        return None
    for phase in phases.phases:
        if phase.name == name:
            if int(phase.end_frame) < int(phase.start_frame):
                return None
            return int(phase.start_frame), int(phase.end_frame)
    return None


def swing_span(phases: SwingPhases | None) -> tuple[int, int] | None:
    """Takeback start -> follow-through end: "the swing", for the range metrics."""
    takeback = phase_span(phases, SwingPhaseName.TAKEBACK)
    follow_through = phase_span(phases, SwingPhaseName.FOLLOW_THROUGH)
    if takeback is None or follow_through is None:
        return None
    if follow_through[1] < takeback[0]:
        return None
    return takeback[0], follow_through[1]


def visible_frames_in(
    seq: NormalizedSequence,
    span: tuple[int, int] | None,
    landmarks: tuple[int, ...],
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> np.ndarray:
    """Frames inside ``span`` where all of ``landmarks`` were visible."""
    if span is None:
        return np.zeros((0,), dtype=np.int64)
    frames = frame_window(seq, span[0], span[1])
    if frames.shape[0] == 0:
        return frames
    mask = visible_mask(seq, landmarks, threshold=threshold)
    return np.asarray(frames[mask[frames]], dtype=np.int64)


# --- individual metrics ------------------------------------------------------


def shoulder_hip_separation_deg(
    seq: NormalizedSequence, phases: SwingPhases | None
) -> float | None:
    """Max SIGNED shoulder-line minus hip-line angle during takeback ("X-factor")."""
    landmarks = (LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP)
    frames = visible_frames_in(seq, phase_span(phases, SwingPhaseName.TAKEBACK), landmarks)
    if frames.shape[0] == 0:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    separations: list[float] = []
    for frame in frames:
        shoulder = line_angle_deg(points[frame, LEFT_SHOULDER], points[frame, RIGHT_SHOULDER])
        hip = line_angle_deg(points[frame, LEFT_HIP], points[frame, RIGHT_HIP])
        if shoulder is None or hip is None:
            continue
        separations.append(signed_angle_difference_deg(shoulder, hip))
    if not separations:
        return None
    return max_absolute_signed(np.asarray(separations, dtype=np.float64))


def _line_angle_range(
    seq: NormalizedSequence,
    phases: SwingPhases | None,
    left_index: int,
    right_index: int,
) -> float | None:
    landmarks = (left_index, right_index)
    frames = visible_frames_in(seq, swing_span(phases), landmarks)
    if frames.shape[0] == 0:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    angles: list[float] = []
    for frame in frames:
        angle = line_angle_deg(points[frame, left_index], points[frame, right_index])
        if angle is not None:
            angles.append(angle)
    if not angles:
        return None
    return angular_range_deg(np.asarray(angles, dtype=np.float64))


def shoulder_turn_deg(seq: NormalizedSequence, phases: SwingPhases | None) -> float | None:
    """Shoulder-line angle range relative to the x-axis, across the swing.

    The Stage 13 table fixes no window for this one; it is matched to
    ``hip_rotation_deg``'s "across the swing" so the two stay comparable.
    """
    return _line_angle_range(seq, phases, LEFT_SHOULDER, RIGHT_SHOULDER)


def hip_rotation_deg(seq: NormalizedSequence, phases: SwingPhases | None) -> float | None:
    """Hip-line angle range across the swing."""
    return _line_angle_range(seq, phases, LEFT_HIP, RIGHT_HIP)


def elbow_angle_at_contact_deg(
    seq: NormalizedSequence, handedness: Handedness, contact_frame: int
) -> float | None:
    """Shoulder-elbow-wrist angle on the racket arm, at contact."""
    left = handedness == Handedness.LEFT
    shoulder = LEFT_SHOULDER if left else RIGHT_SHOULDER
    elbow = LEFT_ELBOW if left else RIGHT_ELBOW
    wrist = racket_wrist_index(handedness)
    if not frame_is_visible(seq, contact_frame, (shoulder, elbow, wrist)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    return joint_angle_deg(
        points[contact_frame, shoulder],
        points[contact_frame, elbow],
        points[contact_frame, wrist],
    )


def wrist_lag_deg(
    seq: NormalizedSequence, handedness: Handedness, contact_frame: int
) -> float | None:
    """Forearm vector vs hand-velocity vector, ``WRIST_LAG_LEAD_FRAMES`` before contact.

    A proxy only: the racket head is not an observable landmark.
    """
    frame = int(contact_frame) - WRIST_LAG_LEAD_FRAMES
    left = handedness == Handedness.LEFT
    elbow = LEFT_ELBOW if left else RIGHT_ELBOW
    wrist = racket_wrist_index(handedness)
    if frame < 0 or not frame_is_visible(seq, frame, (elbow, wrist)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    velocity = np.asarray(seq.velocity, dtype=np.float64)
    if frame >= velocity.shape[0]:
        return None
    forearm = points[frame, wrist] - points[frame, elbow]
    if float(np.linalg.norm(forearm)) < MIN_VECTOR_NORM:
        return None
    if float(np.linalg.norm(velocity[frame, wrist])) < MIN_VECTOR_NORM:
        return None
    return joint_angle_deg(
        forearm, np.zeros(2, dtype=np.float64), velocity[frame, wrist]
    )


def contact_height_ratio(
    seq: NormalizedSequence, handedness: Handedness, contact_frame: int
) -> float | None:
    """Wrist y at contact mapped to 0 = mid-hip, 1 = mid-shoulder.

    y is positive UP and the mid-hip is the origin, so mid-hip y is 0 and
    mid-shoulder y is positive: the ratio is ``wrist_y / mid_shoulder_y``,
    NOT its inverse.
    """
    wrist = racket_wrist_index(handedness)
    if not frame_is_visible(seq, contact_frame, (wrist, LEFT_SHOULDER, RIGHT_SHOULDER)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    shoulder_y = float(
        (points[contact_frame, LEFT_SHOULDER, 1] + points[contact_frame, RIGHT_SHOULDER, 1]) / 2.0
    )
    if abs(shoulder_y) < MIN_VECTOR_NORM:
        return None
    return float(points[contact_frame, wrist, 1]) / shoulder_y


def contact_point_forward_tu(
    seq: NormalizedSequence, handedness: Handedness, contact_frame: int
) -> float | None:
    """Signed wrist x at contact relative to mid-hip, times swing_direction_sign.

    Wrist x is ALREADY mid-hip relative: Stage 7 step 5 put the origin there.
    """
    wrist = racket_wrist_index(handedness)
    if not frame_is_visible(seq, contact_frame, (wrist,)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    return float(points[contact_frame, wrist, 0]) * float(seq.swing_direction_sign)


def swing_path_fit(
    seq: NormalizedSequence, handedness: Handedness, contact_frame: int
) -> tuple[float, float] | None:
    """Least-squares fit of the hand trajectory over [contact-6, contact+3].

    Returns ``(swing_path_angle_deg, swing_plane_deviation_tu)``. Positive angle
    = low-to-high, because y is positive up in this frame.
    """
    wrist = racket_wrist_index(handedness)
    frames = frame_window(
        seq,
        int(contact_frame) - SWING_PATH_FRAMES_BEFORE,
        int(contact_frame) + SWING_PATH_FRAMES_AFTER,
    )
    if frames.shape[0] == 0:
        return None
    mask = visible_mask(seq, (wrist,))
    frames = frames[mask[frames]]
    if frames.shape[0] < MIN_FIT_POINTS:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    return fit_trajectory_line(points[frames, wrist, :])


def front_knee_side(
    seq: NormalizedSequence, contact_frame: int
) -> tuple[int, int, int] | None:
    """``(hip, knee, ankle)`` indices of the FRONT leg at contact, or None.

    "Front" is the knee further toward the target, i.e. the larger
    ``x * swing_direction_sign`` -- consistent with how every other signed-x
    quantity in this pipeline defines forward. The Stage 13 table does not
    define it, and tying it to handedness instead would assume a closed stance.
    """
    points = np.asarray(seq.points, dtype=np.float64)
    if points.shape[0] == 0 or not 0 <= int(contact_frame) < points.shape[0]:
        return None
    sign = float(seq.swing_direction_sign)
    candidates: list[tuple[float, tuple[int, int, int]]] = []
    for hip, knee, ankle in (
        (LEFT_HIP, LEFT_KNEE, LEFT_ANKLE),
        (RIGHT_HIP, RIGHT_KNEE, RIGHT_ANKLE),
    ):
        if frame_is_visible(seq, contact_frame, (hip, knee, ankle)):
            candidates.append((float(points[contact_frame, knee, 0]) * sign, (hip, knee, ankle)))
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry[0])[1]


def knee_flexion_min_deg(
    seq: NormalizedSequence, phases: SwingPhases | None, contact_frame: int
) -> float | None:
    """Minimum front-knee (hip-knee-ankle) angle during the forward swing."""
    side = front_knee_side(seq, contact_frame)
    if side is None:
        return None
    hip, knee, ankle = side
    frames = visible_frames_in(
        seq, phase_span(phases, SwingPhaseName.FORWARD_SWING), (hip, knee, ankle)
    )
    if frames.shape[0] == 0:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    angles = [
        angle
        for angle in (
            joint_angle_deg(points[frame, hip], points[frame, knee], points[frame, ankle])
            for frame in frames
        )
        if angle is not None
    ]
    if not angles:
        return None
    return float(min(angles))


def weight_transfer_tu(seq: NormalizedSequence, phases: SwingPhases | None) -> None:
    """NOT MEASURABLE from a NormalizedSequence. Always None -- see the module note.

    Stage 7 step 5 translates the mid-hip to the origin every frame, so the
    mid-hip x displacement this metric is defined as is identically zero for
    every clip. Reporting 0.0 would be reporting a measurement that was never
    made; ``None`` is the only honest answer until Stage 7 carries the
    pre-translation mid-hip trajectory.
    """
    return None


def balance_sway_tu(seq: NormalizedSequence, phases: SwingPhases | None) -> None:
    """NOT MEASURABLE from a NormalizedSequence. Always None -- see the module note.

    Same cause as :func:`weight_transfer_tu`: the mid-hip is the origin, so its
    positional std-dev is identically zero.
    """
    return None


def follow_through_height_tu(
    seq: NormalizedSequence, handedness: Handedness, contact_frame: int
) -> float | None:
    """Max wrist y after contact, relative to mid-shoulder.

    "Max height" is the MAXIMUM y: y is positive up in this frame.
    """
    wrist = racket_wrist_index(handedness)
    count = int(np.asarray(seq.points, dtype=np.float64).shape[0])
    frames = visible_frames_in(
        seq,
        (int(contact_frame) + 1, count - 1),
        (wrist, LEFT_SHOULDER, RIGHT_SHOULDER),
    )
    if frames.shape[0] == 0:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    shoulder_y = (
        points[frames, LEFT_SHOULDER, 1] + points[frames, RIGHT_SHOULDER, 1]
    ) / 2.0
    return float(np.max(points[frames, wrist, 1] - shoulder_y))


def head_stillness_tu(seq: NormalizedSequence, phases: SwingPhases | None) -> float | None:
    """Std-dev of nose position during the forward swing, in TU."""
    frames = visible_frames_in(
        seq, phase_span(phases, SwingPhaseName.FORWARD_SWING), (NOSE,)
    )
    if frames.shape[0] < 2:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    return position_spread_tu(points[frames, NOSE, :])


def wrist_separation_at_contact_tu(
    seq: NormalizedSequence, contact_frame: int
) -> float | None:
    """Distance between the two wrists at contact. Feeds two-handed detection."""
    if not frame_is_visible(seq, contact_frame, (LEFT_WRIST, RIGHT_WRIST)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    return float(
        np.linalg.norm(points[contact_frame, LEFT_WRIST] - points[contact_frame, RIGHT_WRIST])
    )


# --- orchestrator ------------------------------------------------------------


def compute_swing_metrics(
    seq: NormalizedSequence,
    handedness: HandednessResult,
    contact: ContactDetection,
    phases: SwingPhases | None = None,
) -> tuple[SwingMetrics, list[str]]:
    """Stage 13. NEVER raises; an unmeasurable metric is None, never 0.0.

    Returns ``(metrics, warnings)``. ``warnings`` is free text and empty in the
    common case; it carries the Stage 8 reliability signal that ``SwingMetrics``
    itself cannot hold (every field there is ``float | None`` by a documented
    invariant). This matches the convention already used by
    ``HandednessResult.warnings``, ``PoseQuality.flags`` and
    ``ContactDetection.sanity_flags``: the primary result stays clean, problems
    ride in a sibling list.

    ``takeback_displacement_tu`` is left None: it is in the §2.3 schema and
    Stage 14 reads it, but the Stage 13 metric table never defines it, and
    guessing a definition here would silently feed Stage 14's slice rule.
    """
    metrics = SwingMetrics()
    warnings: list[str] = []
    # Stage 8's own read on the swing, independent of whether a hint later
    # rescued the numeric answer: a weak read still makes every
    # handedness-sensitive metric (elbow angle, wrist lag, anything derived from
    # swing_direction_sign) less trustworthy, and the caller deciding coaching
    # tone should know that.
    if float(handedness.confidence) < HINT_CONFIDENCE_FLOOR:
        hint_applied = handedness.source in (
            HandednessSource.USER_HINT,
            HandednessSource.HINT_OVERRODE_DETECTION,
        )
        warnings.append(
            f"low handedness confidence ({float(handedness.confidence):.2f} < "
            f"{HINT_CONFIDENCE_FLOOR}); "
            f"{'user hint applied' if hint_applied else 'no hint applied'} "
            f"(source {handedness.source.value}); "
            f"handedness-sensitive metrics may be unreliable"
        )
    try:
        hand = handedness.handedness
        contact_frame = int(contact.frame_index)
        fit = swing_path_fit(seq, hand, contact_frame)
        metrics = SwingMetrics(
            shoulder_hip_separation_deg=shoulder_hip_separation_deg(seq, phases),
            shoulder_turn_deg=shoulder_turn_deg(seq, phases),
            hip_rotation_deg=hip_rotation_deg(seq, phases),
            elbow_angle_at_contact_deg=elbow_angle_at_contact_deg(seq, hand, contact_frame),
            wrist_lag_deg=wrist_lag_deg(seq, hand, contact_frame),
            contact_height_ratio=contact_height_ratio(seq, hand, contact_frame),
            contact_point_forward_tu=contact_point_forward_tu(seq, hand, contact_frame),
            # Copied from Stage 9, never recomputed: one reliability rule, one
            # value, so the two can never silently disagree.
            peak_hand_speed_tu_s=contact.peak_hand_speed_tu_s,
            swing_path_angle_deg=None if fit is None else fit[0],
            swing_plane_deviation_tu=None if fit is None else fit[1],
            knee_flexion_min_deg=knee_flexion_min_deg(seq, phases, contact_frame),
            weight_transfer_tu=weight_transfer_tu(seq, phases),
            follow_through_height_tu=follow_through_height_tu(seq, hand, contact_frame),
            balance_sway_tu=balance_sway_tu(seq, phases),
            head_stillness_tu=head_stillness_tu(seq, phases),
            wrist_separation_at_contact_tu=wrist_separation_at_contact_tu(seq, contact_frame),
            takeback_displacement_tu=None,
            tempo_ratio=None if phases is None else phases.tempo_ratio,
        )
    except Exception as error:  # noqa: BLE001 -- Stage 13 contract: cannot raise.
        # The RETURN is deliberately unchanged -- a bare SwingMetrics(), because
        # downstream code depends on Stage 13 never raising. What was missing is
        # that an internal crash and a clip that is legitimately unmeasurable
        # produced byte-identical output: all 18 fields None, warnings untouched.
        # Stage 15 then scores both the same way and nobody can tell a bug from
        # a bad clip. The warning is the only channel that can carry the
        # difference (every SwingMetrics field is float | None by invariant).
        warnings.append(
            f"{INTERNAL_FAILURE_WARNING} ({type(error).__name__}: {error}); "
            "every metric is unavailable because of that failure, NOT because "
            "the clip was unmeasurable"
        )
        return SwingMetrics(), warnings
    return metrics, warnings
