"""Stage 7c: aspect correction, y-flip, mid-hip origin, torso-unit scaling.

PURE (PIPELINE.md 4.4, row 7c). Also hosts the Stage 7 orchestrator
``normalize_sequence``, which applies the nine fixed steps in the order the spec
declares -- the order is load-bearing and must not be rearranged.

The BlazePose landmark indices below belong in ``app/pose/landmarks.py`` once
that module exists (PIPELINE.md 4.4 lists it as pure); they are defined here for
now so that ``app/analysis/`` imports nothing from ``app/pose/``.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.smoothing import (
    MAX_GAP_FRAMES,
    central_difference,
    interpolate_gaps,
    longest_gap_frames,
    savgol_smooth,
)
from app.models.enums import CameraView
from app.models.internal import NormalizedSequence, PoseSequence
from app.models.responses import PoseQuality

NOSE: Final[int] = 0
LEFT_SHOULDER: Final[int] = 11
RIGHT_SHOULDER: Final[int] = 12
LEFT_ELBOW: Final[int] = 13
RIGHT_ELBOW: Final[int] = 14
LEFT_WRIST: Final[int] = 15
RIGHT_WRIST: Final[int] = 16
LEFT_HIP: Final[int] = 23
RIGHT_HIP: Final[int] = 24
LEFT_KNEE: Final[int] = 25
RIGHT_KNEE: Final[int] = 26
LEFT_ANKLE: Final[int] = 27
RIGHT_ANKLE: Final[int] = 28

CORE_LANDMARKS: Final[tuple[int, ...]] = (
    LEFT_SHOULDER, RIGHT_SHOULDER,
    LEFT_ELBOW, RIGHT_ELBOW,
    LEFT_WRIST, RIGHT_WRIST,
    LEFT_HIP, RIGHT_HIP,
    LEFT_KNEE, RIGHT_KNEE,
    LEFT_ANKLE, RIGHT_ANKLE,
)

VISIBILITY_THRESHOLD: Final[float] = 0.5
CENTROID_JUMP_TU: Final[float] = 0.25
TORSO_CHANGE_FRACTION: Final[float] = 0.35
SUBJECT_IDENTITY_UNSTABLE: Final[str] = "subject_identity_unstable"

#: PIPELINE.md 7.1.2, Tier 2. Length of the core sub-window the usability
#: verdict is judged over (+-1.0 s about the anchor). A swing from takeback-end
#: through follow-through runs ~0.6-1.2 s; 2.0 s is 60 frames at 30 fps, which
#: holds all four Stage 12 phases plus margin.
CORE_WINDOW_S: Final[float] = 2.0

#: Bounds TOTAL loss inside the core. 10 % of 60 frames is 6 frames, which
#: (because Tier 1 still refuses any single gap over 3) must arrive as at least
#: two separate short gaps to get this far.
MIN_CORE_COVERAGE: Final[float] = 0.90

#: Bounds CONCENTRATED loss inside the core: the unchanged 3-frame bound, newly
#: SCOPED. Outside the core a longer gap is recorded and flagged, not fatal.
MAX_CORE_GAP_FRAMES: Final[int] = 3

#: How far the coarse anchor may be repositioned to find the best-covered
#: placement of the core window. PIPELINE.md 7.1.2 states the anchor's own
#: accuracy -- "allowed to be half a second wrong" -- and this is that number
#: spent where it was earned. Bounded: a box free to slide further would walk
#: around a hole through contact instead of failing on it.
ANCHOR_TOLERANCE_S: Final[float] = 0.5

DEAD_TIME_OUTSIDE_CORE: Final[str] = "dead_time_outside_core"


def core_visibility(landmarks: np.ndarray) -> np.ndarray:
    """Mean ``visibility`` of the 12 core landmarks, per frame. Shape (T,)."""
    data = np.asarray(landmarks, dtype=np.float64)
    if data.ndim != 3 or data.shape[2] < 4:
        raise ValueError("landmarks must be (T, 33, 4)")
    return np.asarray(data[:, CORE_LANDMARKS, 3].mean(axis=1), dtype=np.float64)


def valid_frames(
    landmarks: np.ndarray,
    detected: np.ndarray,
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> np.ndarray:
    """Stage 7 step 1: valid if detected AND mean core visibility >= threshold."""
    flags = np.asarray(detected, dtype=bool)
    return flags & (core_visibility(landmarks) >= threshold)


def apply_aspect_correction(xy: np.ndarray, width_px: int, height_px: int) -> np.ndarray:
    """Stage 7 step 3: multiply x by width/height so one x unit == one y unit."""
    if width_px <= 0 or height_px <= 0:
        raise ValueError("width_px and height_px must be positive")
    data = np.array(xy, dtype=np.float64, copy=True)
    data[..., 0] *= float(width_px) / float(height_px)
    return data


def flip_y(xy: np.ndarray) -> np.ndarray:
    """Stage 7 step 4: negate y so up is positive."""
    data = np.array(xy, dtype=np.float64, copy=True)
    data[..., 1] *= -1.0
    return data


def mid_hip(xy: np.ndarray) -> np.ndarray:
    """Per-frame mid-hip point, shape (T, 2)."""
    data = np.asarray(xy, dtype=np.float64)
    return (data[:, LEFT_HIP, :] + data[:, RIGHT_HIP, :]) / 2.0


def mid_shoulder(xy: np.ndarray) -> np.ndarray:
    """Per-frame mid-shoulder point, shape (T, 2)."""
    data = np.asarray(xy, dtype=np.float64)
    return (data[:, LEFT_SHOULDER, :] + data[:, RIGHT_SHOULDER, :]) / 2.0


def origin_to_pixels(origin_flipped: np.ndarray, height_px: int) -> np.ndarray:
    """Undo steps 3 and 4 on the mid-hip so it is expressed in image pixels.

    ``origin_flipped`` is the per-frame mid-hip AFTER ``apply_aspect_correction``
    and ``flip_y``, i.e. what step 5 subtracts. Raw landmarks are normalized
    [0, 1] image coordinates with y DOWN, so::

        raw_x = origin_flipped.x / (width_px / height_px)
        raw_y = -origin_flipped.y

    and multiplying by (width_px, height_px) gives pixels. The x factor
    collapses: ``raw_x * width_px == origin_flipped.x * height_px``, so
    ``width_px`` does not appear and cannot be passed in wrong.

    Returns a (T, 2) float64 array in pose-stream pixels, y DOWN.
    """
    if height_px <= 0:
        raise ValueError("height_px must be positive")
    data = np.asarray(origin_flipped, dtype=np.float64).reshape(-1, 2)
    out = np.empty_like(data)
    out[:, 0] = data[:, 0] * float(height_px)
    out[:, 1] = -data[:, 1] * float(height_px)
    return out


def to_body_frame(xy: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """Stage 7 step 5: subtract the per-frame origin from every landmark."""
    data = np.asarray(xy, dtype=np.float64)
    return data - np.asarray(origin, dtype=np.float64)[:, None, :]


def torso_lengths(xy: np.ndarray) -> np.ndarray:
    """Per-frame |mid_shoulder - mid_hip|, shape (T,)."""
    return np.asarray(
        np.linalg.norm(mid_shoulder(xy) - mid_hip(xy), axis=1), dtype=np.float64
    )


def torso_scale(xy: np.ndarray, valid: np.ndarray | None = None) -> float:
    """Stage 7 step 6: one TU = the MEDIAN over frames of the torso length.

    Median, not per-frame: a per-frame scale would inject the foreshortening of
    the torso into every derived velocity.
    """
    lengths = torso_lengths(xy)
    if valid is not None:
        flags = np.asarray(valid, dtype=bool)
        if flags.any():
            lengths = lengths[flags]
    lengths = lengths[lengths > 0.0]
    if lengths.size == 0:
        return 0.0
    return float(np.median(lengths))


def subject_identity_unstable(
    xy: np.ndarray,
    valid: np.ndarray,
    torso_scale_units: float,
    *,
    centroid_jump_tu: float = CENTROID_JUMP_TU,
    torso_change_fraction: float = TORSO_CHANGE_FRACTION,
) -> bool:
    """Stage 7 step 1b: detect MediaPipe re-anchoring onto a different person.

    Between consecutive DETECTED frames, a mid-hip centroid jump above
    ``centroid_jump_tu`` or a torso-length change above
    ``torso_change_fraction`` means the landmarks stopped describing the same
    body. Fails soft: the caller only appends a flag.
    """
    flags = np.asarray(valid, dtype=bool)
    indices = np.flatnonzero(flags)
    if indices.size < 2 or torso_scale_units <= 0.0:
        return False

    data = np.asarray(xy, dtype=np.float64)
    centroids = mid_hip(data)[indices]
    lengths = torso_lengths(data)[indices]

    jumps = np.linalg.norm(np.diff(centroids, axis=0), axis=1) / torso_scale_units
    if np.any(jumps > centroid_jump_tu):
        return True

    previous = lengths[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        changes = np.abs(np.diff(lengths)) / np.where(previous > 0.0, previous, np.nan)
    return bool(np.any(np.nan_to_num(changes, nan=0.0) > torso_change_fraction))


def landmark_visible(
    visibility: np.ndarray, *, threshold: float = VISIBILITY_THRESHOLD
) -> np.ndarray:
    """Per-frame reliability of ONE landmark's position. Shape (T,) bool.

    Stage 7 step 1's gate is the MEAN over the 12 core landmarks, so a frame can
    pass overall while one specific landmark is occluded, motion-blurred or
    hallucinated behind the body. Any metric that integrates a single landmark
    over time needs this per-landmark view instead.
    """
    data = np.asarray(visibility, dtype=np.float64)
    return np.asarray(data >= float(threshold), dtype=bool)


def path_length(
    points: np.ndarray,
    visibility: np.ndarray | None = None,
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> float:
    """Integrated path length sum of |dp| over a (T, 2) trajectory.

    When ``visibility`` (the (T,) column for THIS landmark) is supplied, a step
    is counted only if the landmark was individually visible at BOTH of its
    endpoints. One occluded frame places the landmark somewhere it never was,
    and the accumulation charges that fictional excursion twice -- in and back
    out -- which is large enough to invert a comparison between two landmarks.
    Excluding the step is the only honest option: the distance actually
    travelled while unobserved is unknown, and guessing it is what produced the
    wrong answer in the first place.
    """
    data = np.asarray(points, dtype=np.float64)
    if data.shape[0] < 2:
        return 0.0
    steps = np.linalg.norm(np.diff(data, axis=0), axis=1)
    if visibility is not None:
        visible = landmark_visible(visibility, threshold=threshold)
        steps = steps[visible[1:] & visible[:-1]]
    return float(np.sum(steps))


def speed_series(points: np.ndarray, timestamps_s: np.ndarray) -> np.ndarray:
    """Scalar speed of a (T, 2) trajectory, using the actual dt."""
    velocity = central_difference(np.asarray(points, dtype=np.float64), timestamps_s)
    return np.asarray(np.linalg.norm(velocity, axis=1), dtype=np.float64)


def swing_direction_sign(wrist_points: np.ndarray, timestamps_s: np.ndarray) -> int:
    """Stage 7 step 9: sign of racket-hand x-displacement, takeback-end to contact.

    Contact and takeback-end are Stage 9/12 products, so this uses the
    kinematic proxies for them, neither of which needs a sign to compute:
    contact ~ the peak-speed frame, takeback-end ~ the slowest frame before it
    (the hand pauses at the end of the takeback). Zero displacement gives +1.
    """
    points = np.asarray(wrist_points, dtype=np.float64)
    if points.shape[0] < 2:
        return 1
    speed = speed_series(points, timestamps_s)
    peak = int(np.argmax(speed))
    takeback_end = int(np.argmin(speed[: peak + 1])) if peak > 0 else 0
    displacement = float(points[peak, 0] - points[takeback_end, 0])
    return 1 if displacement >= 0.0 else -1


# --- Tier 1: what may be interpolated (PIPELINE.md 7.1.2) -------------------


def hold_unfillable_gaps(
    filled: np.ndarray,
    valid: np.ndarray,
    *,
    max_gap_frames: int = MAX_GAP_FRAMES,
) -> tuple[np.ndarray, int]:
    """Undo the linear fill across gaps LONGER than ``max_gap_frames``.

    Tier 1 of PIPELINE.md 7.1.2, and the half of the defect that is NOT being
    relaxed: linear interpolation across more than ~100 ms of a swing fabricates
    the trajectory the product exists to measure. ``interpolate_gaps`` fills
    every gap unconditionally, so the long ones are replaced here with an edge
    hold -- the first half of the gap holds the last frame before it, the second
    half holds the first frame after it. A hold is inert and visibly so (Stage
    9's held-coordinate test reads exactly-zero speed); a ramp looks like
    motion that never happened. Those frames stay ``valid=False`` either way.

    Returns:
        (data, interpolated_frame_count) where the count covers only frames
        whose coordinates were genuinely interpolated.
    """
    data = np.array(filled, dtype=np.float64, copy=True)
    flags = np.asarray(valid, dtype=bool)
    if flags.ndim != 1 or data.shape[0] != flags.shape[0]:
        raise ValueError("valid must be 1-D and match filled along axis 0")

    interpolated = 0
    count = int(flags.shape[0])
    index = 0
    while index < count:
        if flags[index]:
            index += 1
            continue
        start = index
        while index < count and not flags[index]:
            index += 1
        end = index  # exclusive
        length = end - start
        bounded = start > 0 and end < count
        if bounded and length <= int(max_gap_frames):
            interpolated += length
            continue
        if bounded:
            middle = start + length // 2
            data[start:middle] = data[start - 1]
            data[middle:end] = data[end]
        # Leading and trailing gaps are already an edge hold out of
        # ``interpolate_gaps`` -- there is nothing on one side to interpolate
        # between -- so they are left alone and never counted as interpolated.
    return data, int(interpolated)


# --- Tier 2: where the verdict is judged (PIPELINE.md 7.1.2) ----------------


def longest_valid_run(valid: np.ndarray) -> tuple[int, int]:
    """``(start, end_exclusive)`` of the longest contiguous run of valid frames.

    ``(0, 0)`` when no frame is valid. Ties go to the earlier run.
    """
    flags = np.asarray(valid, dtype=bool)
    if flags.ndim != 1:
        raise ValueError("valid must be 1-D")
    best = (0, 0)
    index = 0
    count = int(flags.shape[0])
    while index < count:
        if not flags[index]:
            index += 1
            continue
        start = index
        while index < count and flags[index]:
            index += 1
        if index - start > best[1] - best[0]:
            best = (start, index)
    return best


def wrist_extension(xy: np.ndarray) -> np.ndarray:
    """Summed two-wrist distance from mid-hip, per frame, in torso lengths.

    The anchor refinement's proxy for "the busiest moment": both wrists are far
    from the hips during a swing and close to them at rest. Summing the two
    needs no handedness, and dividing by the per-frame torso length makes it
    independent of how big the player is in frame, which changes across a clip
    as the player walks toward or away from the camera. No smoothing: the
    anchor only has to be good to ~half a second.
    """
    data = np.asarray(xy, dtype=np.float64)
    if data.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    hips = mid_hip(data)
    reach = np.linalg.norm(data[:, LEFT_WRIST, :] - hips, axis=1) + np.linalg.norm(
        data[:, RIGHT_WRIST, :] - hips, axis=1
    )
    torso = torso_lengths(data)
    return np.asarray(
        np.where(torso > 0.0, reach / np.where(torso > 0.0, torso, 1.0), 0.0),
        dtype=np.float64,
    )


def window_indices_at(
    timestamps_s: np.ndarray, anchor: int, *, core_window_s: float = CORE_WINDOW_S
) -> tuple[int, int]:
    """``(start, end_exclusive)`` of the ``core_window_s`` window about ``anchor``.

    Placed in TIME, not in frame counts: a 25 fps clip sampled at 30 fps carries
    duplicated timestamps, so "60 frames" is not two seconds and a frame-count
    window would silently under-cover exactly the footage this fix is for. The
    window is +-``core_window_s``/2 about the anchor's timestamp, slid (not
    shrunk) when it would run off an end, and only then truncated -- a sequence
    shorter than ``core_window_s`` yields the whole sequence.
    """
    times = np.asarray(timestamps_s, dtype=np.float64)
    count = int(times.shape[0])
    if count == 0:
        return (0, 0)
    anchor = int(np.clip(anchor, 0, count - 1))
    half = float(core_window_s) / 2.0
    first, last = float(times[0]), float(times[-1])

    low = float(times[anchor]) - half
    high = low + float(core_window_s)
    if low < first:
        low, high = first, min(last, first + float(core_window_s))
    if high > last:
        high, low = last, max(first, last - float(core_window_s))

    start = int(np.searchsorted(times, low, side="left"))
    stop = count if high >= last else int(np.searchsorted(times, high, side="left"))
    start = max(0, min(start, count - 1))
    stop = max(start + 1, min(stop, count))
    return (start, stop)


def core_window_indices(
    valid: np.ndarray,
    timestamps_s: np.ndarray,
    xy: np.ndarray,
    *,
    core_window_s: float = CORE_WINDOW_S,
    anchor_tolerance_s: float = ANCHOR_TOLERANCE_S,
) -> tuple[int, int]:
    """``(start, end_exclusive)`` of the core sub-window the verdict is judged over.

    The anchor is the centre of the longest contiguous valid run, refined --
    when that run is longer than the core window -- to the frame of maximum
    summed two-wrist displacement from mid-hip (``wrist_extension``). The core
    is then the ``core_window_s`` window about the anchor, repositioned by at
    most ``anchor_tolerance_s`` to the placement that covers the most valid
    frames, nearest placement winning ties.

    That last step is the anchor's own stated accuracy spent deliberately: a
    coarse locator good to ~half a second must not be trusted to the frame when
    deciding where the box goes, and on real footage the reach proxy peaks in
    the serve trophy pose ~0.7 s before the strike, which hung a third of the
    box off the front of the valid run and failed a clip whose tracking was
    perfect. The budget is bounded ON PURPOSE: a box free to slide a whole
    second would simply walk around a hole through contact, which is exactly
    the clip Tier 2 must still reject.

    THIS ANCHOR IS A COARSE LOCATOR AND IS NOT CONTACT DETECTION. It exists to
    place a 2 s box and is allowed to be half a second wrong. Contact detection
    is Stage 9's job, it runs on the NormalizedSequence this function helps
    produce, and nothing here may ever be reused as a contact estimate.
    """
    flags = np.asarray(valid, dtype=bool)
    times = np.asarray(timestamps_s, dtype=np.float64)
    count = int(flags.shape[0])
    if count == 0:
        return (0, 0)

    run_start, run_end = longest_valid_run(flags)
    if run_end <= run_start:
        return window_indices_at(times, count // 2, core_window_s=core_window_s)

    anchor = (run_start + run_end - 1) // 2
    core = window_indices_at(times, anchor, core_window_s=core_window_s)
    if (run_end - run_start) > (core[1] - core[0]) and np.asarray(xy).shape[0] == count:
        reach = wrist_extension(xy)
        anchor = run_start + int(np.argmax(reach[run_start:run_end]))

    return _best_placement(
        flags,
        times,
        anchor,
        core_window_s=core_window_s,
        anchor_tolerance_s=anchor_tolerance_s,
    )


def _best_placement(
    valid: np.ndarray,
    timestamps_s: np.ndarray,
    anchor: int,
    *,
    core_window_s: float = CORE_WINDOW_S,
    anchor_tolerance_s: float = ANCHOR_TOLERANCE_S,
) -> tuple[int, int]:
    """The best-covered window within ``anchor_tolerance_s`` of ``anchor``."""
    flags = np.asarray(valid, dtype=bool)
    times = np.asarray(timestamps_s, dtype=np.float64)
    count = int(times.shape[0])
    anchor = int(np.clip(anchor, 0, count - 1))
    anchor_time = float(times[anchor])
    offsets = np.abs(times - anchor_time) <= float(anchor_tolerance_s)

    best: tuple[int, int] = window_indices_at(times, anchor, core_window_s=core_window_s)
    best_key = (core_coverage(flags, best), 0.0)
    for candidate in np.flatnonzero(offsets):
        window = window_indices_at(times, int(candidate), core_window_s=core_window_s)
        key = (
            core_coverage(flags, window),
            -abs(float(times[int(candidate)]) - anchor_time),
        )
        if key > best_key:
            best_key, best = key, window
    return best


def core_coverage(valid: np.ndarray, core: tuple[int, int]) -> float:
    """Share of frames inside ``core`` that passed the visibility gate."""
    flags = np.asarray(valid, dtype=bool)
    start, end = int(core[0]), int(core[1])
    if end <= start:
        return 0.0
    window = flags[start:end]
    if window.size == 0:
        return 0.0
    return float(np.count_nonzero(window) / window.size)


def longest_core_gap(valid: np.ndarray, core: tuple[int, int]) -> int:
    """Longest run of invalid frames INSIDE ``core``."""
    flags = np.asarray(valid, dtype=bool)
    start, end = int(core[0]), int(core[1])
    if end <= start:
        return 0
    return longest_gap_frames(flags[start:end])


def normalize_sequence(
    seq: PoseSequence,
    *,
    max_gap_frames: int = MAX_GAP_FRAMES,
    core_window_s: float = CORE_WINDOW_S,
    min_core_coverage: float = MIN_CORE_COVERAGE,
    max_core_gap_frames: int = MAX_CORE_GAP_FRAMES,
) -> tuple[NormalizedSequence, PoseQuality]:
    """Stage 7, all nine steps, in the declared order. Never raises on bad data.

    Step 1b needs a TU scale before step 6 formally computes one, so the torso
    median is evaluated once on the aspect-corrected coordinates and the SAME
    scalar is reused at step 6; the visible order of operations is unchanged.

    The usability verdict is the two-tier rule of PIPELINE.md 7.1.2.
    ``max_gap_frames`` is now purely an INTERPOLATION limit (Tier 1); the
    verdict (Tier 2) is decided by ``min_core_coverage`` and
    ``max_core_gap_frames`` over a ``core_window_s`` sub-window anchored on the
    longest valid run. A dead stretch outside that core -- which ordinary
    single-camera footage contains by construction, before the player is in
    frame and after they have walked out of it -- is recorded, flagged
    ``dead_time_outside_core``, and tolerated.
    """
    landmarks = np.asarray(seq.landmarks, dtype=np.float64)
    frame_count = int(landmarks.shape[0])
    timestamps_s = np.asarray(seq.timestamps_s, dtype=np.float64)
    visibility = (
        landmarks[:, :, 3].copy() if frame_count else np.zeros((0, 33), dtype=np.float64)
    )

    # 1. visibility gate
    valid = (
        valid_frames(landmarks, seq.detected)
        if frame_count
        else np.zeros((0,), dtype=bool)
    )
    raw_xy = landmarks[:, :, :2]

    # 3. aspect correction, hoisted ahead of the TU-denominated step-1b check so
    #    that the distances compared in step 1b are isotropic.
    corrected = apply_aspect_correction(raw_xy, seq.width_px, seq.height_px)
    scale_units = torso_scale(corrected, valid)

    # 1b. subject-continuity check
    flags: list[str] = []
    if subject_identity_unstable(corrected, valid, scale_units):
        flags.append(SUBJECT_IDENTITY_UNSTABLE)

    # 2. gap interpolation -- TIER 1. Gaps of at most ``max_gap_frames`` are
    #    interpolated; longer ones are edge-held and stay invalid, because
    #    interpolating them would fabricate the swing.
    interpolated, _, longest_gap = interpolate_gaps(
        corrected, valid, max_gap_frames=max_gap_frames
    )
    filled, interpolated_frames = hold_unfillable_gaps(
        interpolated, valid, max_gap_frames=max_gap_frames
    )

    # 2b. TIER 2 -- the usability verdict, judged over the core sub-window.
    core = core_window_indices(valid, timestamps_s, corrected, core_window_s=core_window_s)
    coverage = core_coverage(valid, core)
    core_gap = longest_core_gap(valid, core)
    usable = bool(
        frame_count > 0
        and bool(valid.any())
        and scale_units > 0.0
        and coverage >= float(min_core_coverage)
        and core_gap <= int(max_core_gap_frames)
    )
    if usable and longest_gap > max_gap_frames:
        flags.append(DEAD_TIME_OUTSIDE_CORE)
    core_start_s = float(timestamps_s[core[0]]) if core[1] > core[0] else 0.0
    core_end_s = float(timestamps_s[core[1] - 1]) if core[1] > core[0] else 0.0

    # 4. flip y, 5. mid-hip origin, 6. torso-unit scaling
    flipped = flip_y(filled)
    origin = mid_hip(flipped)
    body = to_body_frame(flipped, origin)
    scaled = body / scale_units if scale_units > 0.0 else body

    # 7. smoothing, 8. derivatives
    points = savgol_smooth(scaled)
    velocity = central_difference(points, timestamps_s)
    acceleration = central_difference(velocity, timestamps_s)

    # 9. swing direction sign, from the longer-path wrist (handedness is Stage 8)
    if frame_count >= 2:
        right_path = path_length(points[:, RIGHT_WRIST, :])
        left_path = path_length(points[:, LEFT_WRIST, :])
        proxy = RIGHT_WRIST if right_path >= left_path else LEFT_WRIST
        sign = swing_direction_sign(points[:, proxy, :], timestamps_s)
    else:
        sign = 1

    torso_scale_px = float(scale_units * float(seq.height_px))
    normalized = NormalizedSequence(
        origin_px=origin_to_pixels(origin, seq.height_px),
        points=points,
        velocity=velocity,
        acceleration=acceleration,
        timestamps_s=timestamps_s,
        valid=valid,
        visibility=np.asarray(visibility, dtype=np.float64),
        swing_direction_sign=int(sign),
        torso_scale_px=torso_scale_px,
        width_px=int(seq.width_px),
        height_px=int(seq.height_px),
    )

    frames_with_pose = int(np.count_nonzero(valid))
    mean_visibility = (
        float(np.clip(core_visibility(landmarks)[valid].mean(), 0.0, 1.0))
        if frames_with_pose
        else 0.0
    )
    quality = PoseQuality(
        frames_with_pose=frames_with_pose,
        frames_missing=int(frame_count - frames_with_pose),
        detection_rate=(frames_with_pose / frame_count) if frame_count else 0.0,
        mean_visibility=mean_visibility,
        longest_gap_frames=int(longest_gap) if frame_count else 0,
        interpolated_frames=int(interpolated_frames),
        torso_scale_px=torso_scale_px,
        core_window_start_s=core_start_s,
        core_window_end_s=core_end_s,
        core_coverage_fraction=float(np.clip(coverage, 0.0, 1.0)),
        longest_core_gap_frames=int(core_gap),
        estimated_camera_view=CameraView.UNKNOWN,
        usable=usable,
        flags=flags,
    )
    return normalized, quality
