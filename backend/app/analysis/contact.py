"""Stage 9: contact-frame detection (PURE, PIPELINE.md 4.4 row 9).

There is no ball landmark at this point in the pipeline, so contact is inferred
from the kinematic signature of the racket hand. Contact detection is pose-only
and is NEVER revised by the ball detector: the dependency runs one way,
contact -> ball window.

Nothing in this module raises. A confidence below 0.35 is the signal the
orchestrator acts on (downgrade to partial, refuse ball speed); it is not a
reason for this module to throw.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.handedness import racket_wrist_index
from app.analysis.normalize import (
    LEFT_SHOULDER,
    RIGHT_SHOULDER,
    SUBJECT_IDENTITY_UNSTABLE,
    VISIBILITY_THRESHOLD,
    landmark_visible,
)
from app.models.enums import Handedness
from app.models.internal import NormalizedSequence
from app.models.responses import ContactDetection, HandednessResult, PoseQuality

METHOD: Final[str] = "peak_speed_decel_onset_v1"

# Stage 9 step 4. The racket-hand speed curve at 25-30 fps is spiky, not smooth:
# a real swing peak routinely loses 15-45 % of its height in the single frame
# after the peak. A "speed fell >= X % below peak" rule therefore fires at
# peak + 1 for every value of X up to ~0.5, which is where the measured 2-frame
# late bias came from -- the percentage was never the binding constraint, the
# first post-peak frame was. The onset is instead the END of the contiguous run
# of frames that are still travelling at SUSTAINED_SPEED_FRACTION of the peak.
SUSTAINED_SPEED_FRACTION: Final[float] = 0.45
# A single elevated frame straight after the peak is not a plateau: the Stage 7
# Savitzky-Golay window of 5 makes any one-frame spike leak into its immediate
# neighbours by construction, so speed[peak + 1] carries the peak's own energy.
# A plateau is only credible once it reaches peak + 2.
MIN_SUSTAINED_RUN_FRAMES: Final[int] = 2
# And a plateau cannot run forever. A broad, slowly-decaying peak stays inside
# the 45 % band for a dozen frames, which is follow-through, not contact: at
# 25-30 fps the whole impact-and-brake event is over inside ~150 ms. Beyond this
# many frames the peak is genuinely broad and contact genuinely ambiguous, so the
# search stops and returns a bounded answer instead of walking into the
# follow-through. Inert on every real clip measured (longest observed plateau: 3).
MAX_SUSTAINED_RUN_FRAMES: Final[int] = 4
PEAK_SEPARATION_FRAMES: Final[int] = 5
PROMINENCE_RATIO_CAP: Final[float] = 10.0
CLIP_EDGE_MARGIN_FRAMES: Final[int] = 4
ARM_EXTENSION_MIN_RATIO: Final[float] = 0.6

# Stage 9 step 3b (PIPELINE.md 9.2). The gates below used to be post-hoc
# discounts on a decision already made: ``peak_speed_index`` returned ONE
# argmax, the plateau walk turned it into a contact index, and only then did the
# gates run. With a single candidate a physically implausible answer cannot
# lose -- there is nothing for it to lose to -- so three independent signals
# saying "this cannot be a contact" only made a wrong answer's confidence small.
# Two of them are now applied BEFORE selection, against an enumerated candidate
# set. See ``FILTER_GATES`` for which two, and why only two.
MAX_CANDIDATES: Final[int] = 5

#: Gates that EXCLUDE a candidate before selection, rather than discounting the
#: winner afterwards (PIPELINE.md 9.2.2). Deliberately only two:
#:
#: * ``wrist_behind_mid_hip`` compares against ZERO -- it is a statement about
#:   physical possibility with no tuned constant in it to be wrong about.
#: * ``arm_not_extended`` is clip-relative, so it filters only when the clip's
#:   own reach range clears ``ARM_RANGE_MIN_TU`` (below that it degrades back to
#:   a penalty, because "60 % of nothing" is not information).
#:
#: ``contact_near_clip_end`` stays a penalty because a real contact genuinely
#: can occur near a clip edge and filtering it would reject CORRECT answers.
#: ``motion_not_sustained`` stays a penalty because it is composite and its
#: strongest sub-test was measured anti-correlated with correctness.
#: ``subject_identity_unstable`` takes the same value for every candidate and so
#: cannot discriminate between them by construction.
FILTER_GATES: Final[frozenset[str]] = frozenset(
    {"wrist_behind_mid_hip", "arm_not_extended"}
)

# The guard on the arm-extension filter. ARM_EXTENSION_MIN_RATIO is a fraction
# of the range of wrist-to-shoulder reach THIS CLIP happens to contain, which is
# what makes it robust to body size and camera distance -- and what makes it
# meaningless when the clip contains no genuine extension at all (a practice
# swing, a motionless hold, a tracker that never found the arm). Rejecting on a
# degenerate denominator converts a low-confidence answer into a job failure for
# zero information gain, so below this floor the gate demotes itself back to a
# confidence penalty.
#
# SET FROM CORPUS MEASUREMENT, not guessed: across the 16-clip corpus the
# observed reach range runs 0.415-1.179 TU, the minimum being
# tennis_forehand_8224596_pexels. Nothing in the corpus lies between 0 and
# 0.415, so 0.15 sits a factor of ~2.8 below the smallest genuine range and the
# filter is permitted on every real clip measured; the guard fires only on a
# range that has actually collapsed (the stationary-player fixture measures
# ~0.0).
ARM_RANGE_MIN_TU: Final[float] = 0.15

#: Every enumerated candidate failed a filter gate. DISTINCT from
#: ``sequence_unusable`` ("fewer than 3 frames, or an internal failure"): here
#: the sequence was perfectly analysable and no frame in it could be a contact.
#: Stage 9 still cannot raise, so this is returned with ``confidence = 0.0`` and
#: the ORCHESTRATOR maps it to ``ErrorCode.CONTACT_NOT_FOUND``
#: (docs/PIPELINE_STAGES_12_14_15.md E.2).
CONTACT_NOT_FOUND: Final[str] = "contact_not_found"

# Confidence is a monotone ramp in prominence_ratio between these two anchors,
# then multiplied by one penalty per sanity gate that fired (Stage 9 step 5).
PROMINENCE_RATIO_FLOOR: Final[float] = 1.1
PROMINENCE_RATIO_CEILING: Final[float] = 2.5
CONFIDENCE_FLOOR: Final[float] = 0.35
GATE_PENALTIES: Final[dict[str, float]] = {
    "wrist_behind_mid_hip": 0.6,
    "arm_not_extended": 0.8,
    "contact_near_clip_end": 0.7,
    SUBJECT_IDENTITY_UNSTABLE: 0.7,
}

# Motion-presence check (separate from GATE_PENALTIES, which is the gate set the
# spec fixes). The gates above test static GEOMETRY only -- where the wrist is
# relative to the hips, how extended the arm is relative to this clip's own
# range -- so a motionless player can satisfy every one of them. This check
# tests that movement is actually happening at the reported frame.
MOTION_SPEED_FRACTION: Final[float] = 0.5
MIN_MOTION_RUN_FRAMES: Final[int] = 2

# The run test above is SELF-REFERENTIAL -- it measures speed against a fraction
# of the very peak it is validating, so it asks "is this peak wide relative to
# itself", which a fabricated spike satisfies by construction. Measured on the
# clip batch it is anti-correlated with correctness: the two studio serves whose
# detection is wrong score runs of 3 and 4 frames, while correctly detected
# forehands score 2. Raising MIN_MOTION_RUN_FRAMES would therefore penalise the
# good clips first. The two tests below supply the evidence the run test cannot:
# both are LOCAL to the candidate and neither can be satisfied by the peak alone.
#
# 1. Approach coverage: a real swing ramps into contact, so the frames just
#    before the event already carry a meaningful share of the eventual speed. A
#    spike out of a static trophy-hold comes from a dead stop.
APPROACH_WINDOW_FRAMES: Final[int] = 6
APPROACH_SPEED_FRACTION: Final[float] = 0.15
MIN_APPROACH_COVERAGE: Final[float] = 0.5
# 2. Held-landmark share: after Stage 7 smoothing, a genuinely tracked landmark
#    on real video never repeats a coordinate exactly. An exact zero speed means
#    the position was HELD -- an interpolated gap or a carried-forward detection.
#    A speed event surrounded by held frames is not corroborated by live
#    tracking. Measured 0.00 on every clip in the batch except the two studio
#    serves whose detection is wrong (0.16 and 0.35).
MOTION_NEIGHBOURHOOD_FRAMES: Final[int] = 40
HELD_LANDMARK_SPEED_TU_S: Final[float] = 1e-6
MAX_HELD_FRACTION: Final[float] = 0.1
# Chosen so that a detection resting on an isolated speed excursion cannot clear
# CONFIDENCE_FLOOR on its own, however clean its prominence ratio looks: without
# sustained motion there is no swing to be confident about.
MOTION_ABSENT_PENALTY: Final[float] = 0.3
MOTION_NOT_SUSTAINED: Final[str] = "motion_not_sustained"
RACKET_WRIST_UNOBSERVED: Final[str] = "racket_wrist_unobserved"


def racket_hand_speed(seq: NormalizedSequence, handedness: Handedness) -> np.ndarray:
    """Stage 9 step 1: racket-hand speed s(t) in TU/s. Shape (T,)."""
    velocity = np.asarray(seq.velocity, dtype=np.float64)
    if velocity.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    wrist = racket_wrist_index(handedness)
    return np.asarray(np.linalg.norm(velocity[:, wrist, :], axis=1), dtype=np.float64)


def racket_wrist_reliable(
    seq: NormalizedSequence,
    handedness: Handedness,
    *,
    threshold: float = VISIBILITY_THRESHOLD,
) -> np.ndarray:
    """Frames whose racket-hand VELOCITY is trustworthy. Shape (T,) bool.

    Velocity at frame i is a central difference over frames i-1 and i+1, so all
    three must have seen the wrist. Stage 7 step 1 only gates on the MEAN
    visibility of 12 landmarks, which one occluded wrist cannot move far: a
    single hallucinated wrist position becomes a velocity spike that is larger
    than any real swing in the clip, and step 2's global argmax then picks it.
    A frame we could not see is not evidence of speed.
    """
    visibility = np.asarray(seq.visibility, dtype=np.float64)
    count = int(np.asarray(seq.velocity, dtype=np.float64).shape[0])
    if visibility.ndim != 2 or visibility.shape[0] != count or count == 0:
        return np.ones((count,), dtype=bool)
    visible = landmark_visible(
        visibility[:, racket_wrist_index(handedness)], threshold=threshold
    )
    if count == 1:
        return visible
    previous = np.concatenate([visible[:1], visible[:-1]])
    following = np.concatenate([visible[1:], visible[-1:]])
    return np.asarray(visible & previous & following, dtype=bool)


def observed_fraction(reliable: np.ndarray) -> float:
    """Share of the window in which the racket-hand velocity could be trusted.

    Every unobserved frame is a frame that could have held the real contact, so
    a detection made over a partly-unseen window is uncertain in exact
    proportion to how much of it was unseen. This is a separate question from
    "is this peak taller than its rivals" (the prominence ratio), and it is the
    one that a clip where MediaPipe never found the racket arm fails.
    """
    flags = np.asarray(reliable, dtype=bool)
    if flags.shape[0] == 0:
        return 0.0
    return float(np.mean(flags))


def peak_speed_index(speed: np.ndarray, reliable: np.ndarray | None = None) -> int:
    """Stage 9 step 2: the global speed maximum, over TRUSTWORTHY frames only.

    Falls back to the unrestricted argmax when no frame is trustworthy -- Stage 9
    must still return something; the motion-presence gate is what says so.
    """
    values = np.asarray(speed, dtype=np.float64)
    if values.shape[0] == 0:
        return 0
    if reliable is not None:
        flags = np.asarray(reliable, dtype=bool)
        if flags.shape == values.shape and flags.any():
            candidates = np.flatnonzero(flags)
            return int(candidates[int(np.argmax(values[candidates]))])
    return int(np.argmax(values))


def sustained_motion_frames(
    speed: np.ndarray,
    frame_index: int,
    reference_speed: float,
    *,
    reliable: np.ndarray | None = None,
    fraction: float = MOTION_SPEED_FRACTION,
) -> int:
    """Length of the unbroken run of moving, trustworthy frames containing ``frame_index``.

    "Moving" means speed at or above ``fraction`` of ``reference_speed``. A real
    swing sustains elevated speed across several consecutive frames; an isolated
    frame of speed -- which is what a tracking discontinuity looks like once it
    has been through the smoother -- does not. Returns 0 when the frame itself
    is not both moving and trustworthy.
    """
    values = np.asarray(speed, dtype=np.float64)
    count = int(values.shape[0])
    if count == 0:
        return 0
    index = int(np.clip(frame_index, 0, count - 1))
    limit = float(reference_speed) * float(fraction)
    flags = (
        np.asarray(reliable, dtype=bool)
        if reliable is not None and np.asarray(reliable).shape == values.shape
        else np.ones((count,), dtype=bool)
    )
    moving = (values >= limit) & flags
    if not bool(moving[index]):
        return 0
    first = index
    while first > 0 and bool(moving[first - 1]):
        first -= 1
    last = index
    while last < count - 1 and bool(moving[last + 1]):
        last += 1
    return last - first + 1


def approach_motion_coverage(
    speed: np.ndarray,
    frame_index: int,
    reference_speed: float,
    *,
    reliable: np.ndarray | None = None,
    window: int = APPROACH_WINDOW_FRAMES,
    fraction: float = APPROACH_SPEED_FRACTION,
) -> float:
    """Share of the ``window`` frames BEFORE ``frame_index`` that were already moving.

    "Already moving" means trustworthy and at or above ``fraction`` of
    ``reference_speed``. Unlike :func:`sustained_motion_frames` this cannot be
    satisfied by the event itself: the candidate frame and everything after it
    are excluded, so the question is purely whether the hand ramped into the
    event or arrived at it from rest. A swing accelerates through a takeback and
    a forward swing; a landmark that snaps after a long static hold does not.

    Returns 0.0 when there are no frames before ``frame_index``.
    """
    values = np.asarray(speed, dtype=np.float64)
    count = int(values.shape[0])
    if count == 0:
        return 0.0
    index = int(np.clip(frame_index, 0, count - 1))
    first = max(0, index - max(int(window), 0))
    if index <= first:
        return 0.0
    flags = (
        np.asarray(reliable, dtype=bool)
        if reliable is not None and np.asarray(reliable).shape == values.shape
        else np.ones((count,), dtype=bool)
    )
    limit = float(reference_speed) * float(fraction)
    segment = values[first:index]
    trusted = flags[first:index]
    return float(np.mean((segment >= limit) & trusted))


def held_landmark_fraction(
    speed: np.ndarray,
    frame_index: int,
    *,
    reliable: np.ndarray | None = None,
    window: int = MOTION_NEIGHBOURHOOD_FRAMES,
    epsilon: float = HELD_LANDMARK_SPEED_TU_S,
) -> float:
    """Share of the trustworthy frames near ``frame_index`` whose position was HELD.

    A landmark that MediaPipe actually localised on real video jitters: its
    smoothed speed is small but never exactly zero. Exactly zero means the
    coordinate was repeated -- a Stage 7 interpolated gap, or a detection the
    tracker carried forward instead of re-measuring. Those frames are not
    evidence about the subject's motion at all, and a speed event embedded in
    them is not corroborated by live tracking.

    Frames already marked untrustworthy are excluded from both the numerator and
    the denominator: they are accounted for by :func:`observed_fraction`.
    Returns 0.0 when the neighbourhood holds no trustworthy frame.
    """
    values = np.asarray(speed, dtype=np.float64)
    count = int(values.shape[0])
    if count == 0:
        return 0.0
    index = int(np.clip(frame_index, 0, count - 1))
    span = max(int(window), 0)
    first = max(0, index - span)
    last = min(count, index + span + 1)
    flags = (
        np.asarray(reliable, dtype=bool)
        if reliable is not None and np.asarray(reliable).shape == values.shape
        else np.ones((count,), dtype=bool)
    )
    trusted = flags[first:last]
    if not trusted.any():
        return 0.0
    segment = values[first:last][trusted]
    return float(np.mean(np.abs(segment) < float(epsilon)))


def motion_evidence_absent(
    speed: np.ndarray,
    frame_index: int,
    reference_speed: float,
    *,
    reliable: np.ndarray | None = None,
) -> bool:
    """True when nothing near ``frame_index`` evidences a real swing.

    Three independent ways to fail, because a detection resting on a fabricated
    speed event can look healthy on any one of them alone:

    * the event is a single isolated frame (:func:`sustained_motion_frames`),
    * the hand was at rest going into it (:func:`approach_motion_coverage`),
    * the surrounding landmark stream is partly held rather than tracked
      (:func:`held_landmark_fraction`).

    Deliberately biased toward silence -- the comparisons are strict, so a clip
    sitting exactly on a threshold passes. A motion gate that fires on a good
    swing costs more than one that stays quiet on a bad one: below
    ``CONFIDENCE_FLOOR`` the orchestrator downgrades the response to partial and
    refuses to run ball speed.
    """
    run = sustained_motion_frames(
        speed, frame_index, reference_speed, reliable=reliable
    )
    if run < MIN_MOTION_RUN_FRAMES:
        return True
    coverage = approach_motion_coverage(
        speed, frame_index, reference_speed, reliable=reliable
    )
    if coverage < MIN_APPROACH_COVERAGE:
        return True
    return held_landmark_fraction(speed, frame_index, reliable=reliable) > MAX_HELD_FRACTION


def forward_swing_window_start(forward_displacement: np.ndarray, peak_index: int) -> int:
    """Stage 9 step 3: the last local minimum of forward displacement before the peak.

    Frames before this index belong to the takeback and are excluded from the
    contact search. Returns 0 when no interior minimum exists.
    """
    values = np.asarray(forward_displacement, dtype=np.float64)
    peak = int(np.clip(peak_index, 0, max(values.shape[0] - 1, 0)))
    start = 0
    for index in range(1, peak):
        if values[index] <= values[index - 1] and values[index] < values[index + 1]:
            start = index
    return start


def find_deceleration_onset(
    speed: np.ndarray,
    peak_index: int,
    *,
    sustained_fraction: float = SUSTAINED_SPEED_FRACTION,
    min_run_frames: int = MIN_SUSTAINED_RUN_FRAMES,
    max_run_frames: int = MAX_SUSTAINED_RUN_FRAMES,
    search_start: int = 0,
) -> int:
    """Stage 9 step 4: the end of the peak's sustained-speed plateau.

    Contact is the deceleration ONSET, which is the frame the hand stops
    travelling at swing speed -- not the first frame it dips at all. Those are
    the same thing only on a smooth curve, and the real racket-hand speed curve
    is not smooth: it commonly halves in the one frame after its maximum and
    then recovers. A cumulative percentage-drop rule reads that first dip as the
    onset and reports contact one frame past a peak that was itself already a
    frame late, which is the 2-frame late bias.

    So: walk forward from the peak while speed stays at or above
    ``sustained_fraction`` of the peak, and return the last such frame. If that
    run does not reach ``min_run_frames`` there is no plateau -- the peak was a
    genuine spike and is itself the onset, so the peak is returned rather than
    overshooting into post-swing noise. The walk stops after ``max_run_frames``
    so that a broad, slowly-decaying peak cannot drag the answer into the
    follow-through. If the clip ends before the hand decelerates, the last frame
    is returned -- which then trips the ``contact_near_clip_end`` gate and
    depresses confidence, rather than pretending to know.
    """
    values = np.asarray(speed, dtype=np.float64)
    count = values.shape[0]
    if count == 0:
        return 0
    peak = int(np.clip(peak_index, 0, count - 1))
    first = max(peak, int(max(search_start, 0)))
    threshold = float(values[peak]) * float(sustained_fraction)

    limit = min(count, first + 1 + max(int(max_run_frames), 0))
    index = first + 1
    while index < limit and float(values[index]) >= threshold:
        index += 1
    run_length = index - (first + 1)
    if index >= count:
        return count - 1
    if run_length >= int(min_run_frames):
        return index - 1
    return first


def peak_prominence_ratio(
    speed: np.ndarray,
    peak_index: int,
    *,
    min_separation_frames: int = PEAK_SEPARATION_FRAMES,
    cap: float = PROMINENCE_RATIO_CAP,
    reliable: np.ndarray | None = None,
) -> float:
    """Stage 9 step 6: peak speed / the second-highest WELL-SEPARATED peak.

    "Well separated" means a local maximum at least ``min_separation_frames``
    away from the global peak -- without that rule the shoulder of the same peak
    is picked up and the ratio collapses to ~1.0 on a perfectly clean swing.
    A clip with no rival peak at all returns ``cap``.

    ``reliable`` restricts the rivals to frames where the racket wrist was
    actually visible, for the same reason step 2 restricts the peak: an
    occlusion spike is not a rival swing, and letting one in drives the ratio
    below 1.0 and buries a real detection at the confidence floor.
    """
    values = np.asarray(speed, dtype=np.float64)
    count = values.shape[0]
    if count == 0:
        return 0.0
    peak = int(np.clip(peak_index, 0, count - 1))
    peak_speed = float(values[peak])
    if peak_speed <= 0.0:
        return 0.0

    flags = (
        np.asarray(reliable, dtype=bool)
        if reliable is not None and np.asarray(reliable).shape == values.shape
        else np.ones((count,), dtype=bool)
    )
    if not flags.any():
        flags = np.ones((count,), dtype=bool)

    rivals: list[float] = []
    for index in range(count):
        if abs(index - peak) < max(int(min_separation_frames), 1):
            continue
        if not bool(flags[index]):
            continue
        left = values[index - 1] if index > 0 else -np.inf
        right = values[index + 1] if index < count - 1 else -np.inf
        if values[index] >= left and values[index] >= right:
            rivals.append(float(values[index]))

    if not rivals:
        return float(cap)
    second = max(rivals)
    if second <= 0.0:
        return float(cap)
    return float(min(peak_speed / second, cap))


def confidence_from_prominence(prominence_ratio: float) -> float:
    """Monotone ramp from the prominence ratio into [CONFIDENCE_FLOOR, 1.0]."""
    span = PROMINENCE_RATIO_CEILING - PROMINENCE_RATIO_FLOOR
    fraction = (float(prominence_ratio) - PROMINENCE_RATIO_FLOOR) / span
    fraction = float(np.clip(fraction, 0.0, 1.0))
    return float(CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * fraction)


def arm_extension_ratio(
    seq: NormalizedSequence, handedness: Handedness, frame_index: int
) -> float:
    """Wrist-to-shoulder distance at ``frame_index``, scaled to the range of this clip.

    1.0 = the most extended frame in the clip, 0.0 = the most flexed. Relative
    to the clip rather than to an absolute TU threshold, because reach depends
    on camera view and body size.

    The min/max that define the range are taken over frames where the wrist and
    shoulder were individually visible, so an occluded frame cannot widen the
    range and make every other frame look flexed by comparison. A candidate
    frame that was itself not visible scores 0.0: an arm nobody saw is not an
    extended arm.
    """
    points = np.asarray(seq.points, dtype=np.float64)
    if points.shape[0] == 0:
        return 0.0
    wrist = racket_wrist_index(handedness)
    shoulder = LEFT_SHOULDER if handedness == Handedness.LEFT else RIGHT_SHOULDER
    distances = np.linalg.norm(points[:, wrist, :] - points[:, shoulder, :], axis=1)
    index = int(np.clip(frame_index, 0, distances.shape[0] - 1))

    visibility = np.asarray(seq.visibility, dtype=np.float64)
    observed = np.ones((distances.shape[0],), dtype=bool)
    if visibility.ndim == 2 and visibility.shape[0] == distances.shape[0]:
        observed = landmark_visible(visibility[:, wrist]) & landmark_visible(
            visibility[:, shoulder]
        )
    if not observed.any():
        return 0.0
    if not bool(observed[index]):
        return 0.0

    in_range = distances[observed]
    lowest = float(np.min(in_range))
    highest = float(np.max(in_range))
    if highest - lowest <= 0.0:
        return 1.0
    return float(np.clip((float(distances[index]) - lowest) / (highest - lowest), 0.0, 1.0))


def arm_extension_range_tu(seq: NormalizedSequence, handedness: Handedness) -> float:
    """Span of observed wrist-to-shoulder distance across the clip, in TU.

    This is the denominator :func:`arm_extension_ratio` divides by. It is
    returned separately because a filter must know whether its own denominator
    is meaningful: a clip whose reach never changes has no "60 % of the range"
    to be below. Measured over frames where the wrist AND the shoulder were
    individually visible, for the same reason the ratio is. 0.0 when nothing was
    observed.
    """
    points = np.asarray(seq.points, dtype=np.float64)
    if points.shape[0] == 0:
        return 0.0
    wrist = racket_wrist_index(handedness)
    shoulder = LEFT_SHOULDER if handedness == Handedness.LEFT else RIGHT_SHOULDER
    distances = np.linalg.norm(points[:, wrist, :] - points[:, shoulder, :], axis=1)

    visibility = np.asarray(seq.visibility, dtype=np.float64)
    observed = np.ones((distances.shape[0],), dtype=bool)
    if visibility.ndim == 2 and visibility.shape[0] == distances.shape[0]:
        observed = landmark_visible(visibility[:, wrist]) & landmark_visible(
            visibility[:, shoulder]
        )
    if not observed.any():
        return 0.0
    in_range = distances[observed]
    return float(np.max(in_range) - np.min(in_range))


def candidate_peak_indices(
    speed: np.ndarray,
    reliable: np.ndarray,
    *,
    window_start: int,
    separation: int = PEAK_SEPARATION_FRAMES,
    max_candidates: int = MAX_CANDIDATES,
) -> list[int]:
    """Stage 9 step 3b: the plausible speed peaks, best first.

    Local maxima of ``speed`` at or after ``window_start``, restricted to
    visibility-reliable frames, ordered by descending speed, each at least
    ``separation`` frames from every candidate already taken, capped at
    ``max_candidates``.

    ``separation`` defaults to ``PEAK_SEPARATION_FRAMES`` -- the SAME constant
    step 6's prominence calculation uses for "well separated". The two mean the
    same thing ("this is a different swing event, not the shoulder of the one I
    already have") and are deliberately one constant rather than two with the
    same meaning drifting apart.

    Falls back to ignoring reliability when no frame in the window is reliable,
    and to the bare :func:`peak_speed_index` when the window contains no local
    maximum at all: enumeration must never return nothing where the old single
    ``argmax`` returned something, or the filter would be rejecting candidates
    that were never offered.
    """
    values = np.asarray(speed, dtype=np.float64)
    count = int(values.shape[0])
    if count == 0:
        return []
    start = int(np.clip(window_start, 0, count - 1))

    flags = np.asarray(reliable, dtype=bool)
    if flags.shape != values.shape:
        flags = np.ones((count,), dtype=bool)
    if not flags[start:].any():
        flags = np.ones((count,), dtype=bool)

    maxima: list[int] = []
    for index in range(start, count):
        if not bool(flags[index]):
            continue
        left = values[index - 1] if index > 0 else -np.inf
        right = values[index + 1] if index < count - 1 else -np.inf
        if values[index] >= left and values[index] >= right:
            maxima.append(index)

    gap = max(int(separation), 1)
    limit = max(int(max_candidates), 1)
    chosen: list[int] = []
    for index in sorted(maxima, key=lambda i: (-float(values[i]), i)):
        if any(abs(index - taken) < gap for taken in chosen):
            continue
        chosen.append(index)
        if len(chosen) >= limit:
            break
    if not chosen:
        return [peak_speed_index(values, reliable)]
    return chosen


def candidate_rejection_flags(
    seq: NormalizedSequence,
    handedness: Handedness,
    index: int,
) -> list[str]:
    """Which ``FILTER_GATES`` reject the candidate at ``index``. Empty = accepted.

    **A candidate is rejected only when the gate fails at ``index - 1``,
    ``index`` AND ``index + 1`` -- all three.** This is the binding design
    constraint of the change (PIPELINE.md 9.2.4), not a softening of it. Stage 9
    carries an accepted, unresolved ``+1`` frame residual, the filter runs on the
    plateau-walked index, and so that residual sits INSIDE the quantity being
    filtered. Judging the true contact frame one frame late -- when the arm has
    begun to fold, or the wrist has begun to cross back -- would reject the
    correct answer, and a filter that rejects correct answers is strictly worse
    than the defect it replaces. The three-frame rule mirrors
    :func:`racket_wrist_reliable`, so it is the existing discipline rather than a
    new one, and it makes a one-frame dip below threshold unable to reject
    anything.

    Indices are clamped at the clip edges, so a candidate on the first or last
    frame is judged on the neighbours it has.
    """
    points = np.asarray(seq.points, dtype=np.float64)
    count = int(points.shape[0])
    if count == 0:
        return []
    centre = int(np.clip(index, 0, count - 1))
    window = [int(np.clip(centre + offset, 0, count - 1)) for offset in (-1, 0, 1)]

    flags: list[str] = []
    wrist = racket_wrist_index(handedness)
    forward = points[:, wrist, 0] * float(seq.swing_direction_sign)
    if all(float(forward[frame]) <= 0.0 for frame in window):
        flags.append("wrist_behind_mid_hip")

    # The clip-relative gate only gets to REJECT when its own denominator is
    # real; below the floor it is left to the caller as a penalty.
    if arm_extension_range_tu(seq, handedness) >= ARM_RANGE_MIN_TU and all(
        arm_extension_ratio(seq, handedness, frame) < ARM_EXTENSION_MIN_RATIO
        for frame in window
    ):
        flags.append("arm_not_extended")
    return flags


def select_contact_index(
    seq: NormalizedSequence,
    handedness: Handedness,
    speed: np.ndarray,
    reliable: np.ndarray,
    *,
    window_start: int,
) -> tuple[int | None, list[str], list[tuple[int, list[str]]]]:
    """Stage 9 steps 3b-4: the first plausible candidate's contact frame.

    Each enumerated peak is walked through the existing plateau rule of step 4
    FIRST, and the filter is then applied to the frame that candidate would
    actually return -- not to the peak. Filtering the peak would judge a frame
    the function is not going to return, and the plateau walk moves the answer by
    up to ``MAX_SUSTAINED_RUN_FRAMES``.

    Returns ``(contact_index, flags, audit)``:

    * ``contact_index`` -- the winner, or ``None`` when every candidate was
      rejected.
    * ``flags`` -- the sanity flags SELECTION itself contributes: empty on a
      win, ``[CONTACT_NOT_FOUND]`` when nothing passed.
    * ``audit`` -- every candidate considered, in the order considered, paired
      with the flags that rejected it (empty for the winner). This is not
      decoration: a filter whose rejections are invisible is how an
      all-rejected rate becomes a mystery instead of a measurement.

    Candidates whose plateau walks converge on the same frame are one candidate.
    """
    candidates = candidate_peak_indices(speed, reliable, window_start=window_start)
    audit: list[tuple[int, list[str]]] = []
    seen: set[int] = set()
    for peak in candidates:
        contact_index = find_deceleration_onset(speed, peak, search_start=window_start)
        if contact_index in seen:
            continue
        seen.add(contact_index)
        rejected = candidate_rejection_flags(seq, handedness, contact_index)
        audit.append((contact_index, rejected))
        if not rejected:
            return contact_index, [], audit
    return None, [CONTACT_NOT_FOUND], audit


def _empty_detection(analysis_window_start_s: float) -> ContactDetection:
    return ContactDetection(
        frame_index=0,
        time_s=0.0,
        contact_absolute_time_s=float(analysis_window_start_s),
        confidence=0.0,
        peak_hand_speed_tu_s=None,
        peak_frame_index=0,
        prominence_ratio=0.0,
        method=METHOD,
        sanity_flags=["sequence_unusable"],
    )


def _not_found_detection(
    analysis_window_start_s: float,
    *,
    peak_frame_index: int,
    peak_speed_tu_s: float,
    prominence_ratio: float,
) -> ContactDetection:
    """Every candidate was implausible: return NOTHING rather than a known-wrong frame.

    This raises the job-failure rate, and that is the correct trade rather than a
    general preference for honesty. A wrong contact frame does not stay local: it
    sets Stage 12's phase boundaries, therefore every Stage 13 metric, therefore
    Stage 14's shot type, Stage 15's score and Stage 16's coaching text -- all of
    which come out looking entirely normal and are entirely wrong. A
    ``contact_not_found`` is locally diagnosable; a wrong frame is not.

    ``frame_index`` is 0 and ``time_s`` is 0.0 because there is no answer -- the
    peak diagnostics are carried so the refusal can be investigated.
    """
    return ContactDetection(
        frame_index=0,
        time_s=0.0,
        contact_absolute_time_s=float(analysis_window_start_s),
        confidence=0.0,
        peak_hand_speed_tu_s=float(peak_speed_tu_s),
        peak_frame_index=int(peak_frame_index),
        prominence_ratio=float(prominence_ratio),
        method=METHOD,
        sanity_flags=[CONTACT_NOT_FOUND],
    )


def detect_contact_frame(
    seq: NormalizedSequence,
    handedness: HandednessResult,
    quality: PoseQuality | None = None,
    *,
    analysis_window_start_s: float = 0.0,
) -> ContactDetection:
    """Stage 9, the fixed algorithm. NEVER raises.

    ``contact_absolute_time_s = analysis_window_start_s + time_s`` is computed
    once, here, and carried explicitly: Stage 10 seeks into the ORIGINAL file
    and needs an absolute PTS. On a 60 s clip with an 8 s window, confusing it
    with ``time_s`` measures the wrong 250 ms -- a bug invisible on short test
    clips.
    """
    try:
        speed = racket_hand_speed(seq, handedness.handedness)
        count = int(speed.shape[0])
        if count < 3:
            return _empty_detection(analysis_window_start_s)

        # 2. global maximum, over frames where the racket wrist was actually seen
        reliable = racket_wrist_reliable(seq, handedness.handedness)
        peak_index = peak_speed_index(speed, reliable)
        peak_speed = float(speed[peak_index])

        # 3. forward-swing window
        points = np.asarray(seq.points, dtype=np.float64)
        wrist = racket_wrist_index(handedness.handedness)
        forward = points[:, wrist, 0] * float(seq.swing_direction_sign)
        window_start = forward_swing_window_start(forward, peak_index)

        # 3b/4. enumerate candidates, plateau-walk each, and filter the SET
        # before selecting. The gates used to run after this point, on a
        # decision already made; with one candidate an implausible winner had
        # nothing to lose to (PIPELINE.md 9.2).
        candidates = candidate_peak_indices(speed, reliable, window_start=window_start)
        contact_index, selection_flags, _audit = select_contact_index(
            seq, handedness.handedness, speed, reliable, window_start=window_start
        )
        if contact_index is None:
            return _not_found_detection(
                analysis_window_start_s,
                peak_frame_index=peak_index,
                peak_speed_tu_s=peak_speed,
                prominence_ratio=peak_prominence_ratio(
                    speed, peak_index, reliable=reliable
                ),
            )
        # The winner need not be the global argmax any more, so every quantity
        # keyed to "the peak" is re-keyed to the peak that WON.
        peak_index = next(
            (
                candidate
                for candidate in candidates
                if find_deceleration_onset(
                    speed, candidate, search_start=window_start
                )
                == contact_index
            ),
            peak_index,
        )
        peak_speed = float(speed[peak_index])

        # 6. prominence, discounted by how much of the window was observable
        prominence_ratio = peak_prominence_ratio(speed, peak_index, reliable=reliable)
        observed = observed_fraction(reliable)
        confidence = confidence_from_prominence(prominence_ratio) * observed

        # 5. sanity gates -- each lowers confidence, none rejects
        sanity_flags: list[str] = []
        if float(forward[contact_index]) <= 0.0:
            sanity_flags.append("wrist_behind_mid_hip")
        if arm_extension_ratio(seq, handedness.handedness, contact_index) < ARM_EXTENSION_MIN_RATIO:
            sanity_flags.append("arm_not_extended")
        if (
            contact_index < CLIP_EDGE_MARGIN_FRAMES
            or contact_index > count - 1 - CLIP_EDGE_MARGIN_FRAMES
        ):
            sanity_flags.append("contact_near_clip_end")
        if quality is not None and SUBJECT_IDENTITY_UNSTABLE in quality.flags:
            sanity_flags.append(SUBJECT_IDENTITY_UNSTABLE)

        # 5b. motion presence -- the gates above are all static geometry, so a
        # motionless player passes every one of them. This one asks for positive
        # evidence, local to the detection, that a swing actually happened: the
        # event lasts more than one frame, the hand was already moving into it,
        # and the landmark stream around it was tracked rather than held.
        # Measured at the PEAK, not at the deceleration onset: the peak is the
        # leading edge of the contact event, and a hard, genuine swing can drop
        # below half its peak in the very next frame, which is the signature of a
        # real hit rather than of an absent one.
        if motion_evidence_absent(speed, peak_index, peak_speed, reliable=reliable):
            sanity_flags.append(MOTION_NOT_SUSTAINED)
        if observed <= 0.0:
            sanity_flags.append(RACKET_WRIST_UNOBSERVED)

        for flag in sanity_flags:
            confidence *= GATE_PENALTIES.get(flag, 1.0)
        if MOTION_NOT_SUSTAINED in sanity_flags:
            confidence *= MOTION_ABSENT_PENALTY
        confidence = float(np.clip(confidence, 0.0, 1.0))

        timestamps_s = np.asarray(seq.timestamps_s, dtype=np.float64)
        time_s = float(timestamps_s[contact_index] - timestamps_s[0])
        return ContactDetection(
            frame_index=contact_index,
            time_s=time_s,
            contact_absolute_time_s=float(analysis_window_start_s) + time_s,
            confidence=confidence,
            peak_hand_speed_tu_s=peak_speed,
            peak_frame_index=peak_index,
            prominence_ratio=prominence_ratio,
            method=METHOD,
            sanity_flags=sanity_flags,
        )
    except Exception:  # noqa: BLE001 -- Stage 9 contract: cannot raise.
        return _empty_detection(analysis_window_start_s)
