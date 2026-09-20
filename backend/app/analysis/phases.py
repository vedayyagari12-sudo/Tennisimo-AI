"""Stage 12: swing-phase segmentation (PURE, PIPELINE.md 4.4 row 12).

Arrays and value objects in, a ``SwingPhases`` out. No I/O, no MediaPipe, no
network. Nothing here raises: :func:`segment_swing_phases` wraps the whole
computation and returns ``(None, warnings)``.

``None`` IS A SUPPORTED ANSWER
------------------------------
A fabricated segmentation is not. Five phases bracketing a contact frame that
the speed curve does not support would be consumed as fact by five Stage 13
metrics (``metrics.py:342,365,532,597,683``), each of which would then report a
real-looking number measured over a made-up window. When the boundary signals do
not support segmentation this module returns ``None`` and says why.

THE COLLAPSED-PHASE ENCODING IS LOAD-BEARING
--------------------------------------------
``SwingPhase`` carries INCLUSIVE integer frame bounds (``responses.py:156-157``),
and in an inclusive range the only way to write an EMPTY span is
``end_frame == start_frame - 1``. That is also exactly -- and only -- the
condition ``metrics.phase_span`` treats as unusable (``metrics.py:300``), so a
collapsed phase correctly yields ``None`` from every metric keyed on it.

The tempting alternative, ``start_frame == end_frame``, is a TRAP:
``phase_span`` accepts it and hands back a ONE-FRAME window, and
``knee_flexion_min_deg`` (``metrics.py:523-547``) has no minimum-length guard --
it would report the knee angle at a single instant as "minimum flexion during
the forward swing". Do not "fix" ``end_frame = start_frame - 1`` because it
looks like an off-by-one.

Contiguity survives the encoding: phases are built from a non-decreasing vector
of boundary frames, so ``next.start_frame == prev.end_frame + 1`` holds
unconditionally, including across collapses.

WHAT A ``None`` COSTS
---------------------
``preparation`` and ``balance`` lose every live metric they have and score
``None`` (see ``analysis/rubric.py``). Absent segmentation is supported;
HABITUALLY absent segmentation is a product failure, not graceful degradation.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.contact import (
    peak_speed_index,
    racket_hand_speed,
    racket_wrist_reliable,
)
from app.analysis.handedness import racket_wrist_index
from app.models.enums import Handedness, SwingPhaseName
from app.models.internal import NormalizedSequence
from app.models.responses import (
    ContactDetection,
    HandednessResult,
    SwingPhase,
    SwingPhases,
)

# INHERITED FROM PIPELINE.md, NONE EMPIRICALLY VALIDATED against this
# pipeline's own normalization. They are thresholds on a fraction of this
# clip's own peak speed, which is the only thing that makes them defensible
# without tuning: they are scale-free.
READY_EXIT_FRACTION: Final[float] = 0.10  # PIPELINE.md:924
FOLLOW_EXIT_FRACTION: Final[float] = 0.15  # PIPELINE.md:928
CONTACT_HALF_WIDTH: Final[int] = 1  # PIPELINE.md:927, "contact frame +/- 1"
MIN_PHASE_FRAMES: Final[int] = 2  # PIPELINE.md:930, "< 2 frames" is degenerate

# Five phases cannot be ordered across fewer than five frames even if every one
# of them collapses.
MIN_SEGMENTABLE_FRAMES: Final[int] = 5
# Stage 9's degenerate return (contact.py:505-516). Checked EXPLICITLY rather
# than inferred from confidence == 0.0: it carries frame_index = 0, and
# segmenting around frame 0 gives a negative-length ready phase and an empty
# takeback slice.
SEQUENCE_UNUSABLE: Final[str] = "sequence_unusable"
# PIPELINE.md:553 / speed.py:71. Below this Stage 9's own answer is shaky, and
# the phases inherit that shakiness wholesale.
LOW_CONTACT_CONFIDENCE: Final[float] = 0.35

INTERNAL_FAILURE_WARNING: Final[str] = "swing-phase segmentation failed internally"

PHASE_ORDER: Final[tuple[SwingPhaseName, ...]] = (
    SwingPhaseName.READY,
    SwingPhaseName.TAKEBACK,
    SwingPhaseName.FORWARD_SWING,
    SwingPhaseName.CONTACT,
    SwingPhaseName.FOLLOW_THROUGH,
)


# --- boundary searches (pure, each independently testable) -------------------


def first_reliable_frame_at_or_above(
    speed: np.ndarray, reliable: np.ndarray, threshold: float, start: int = 0
) -> int | None:
    """First frame at or after ``start``, RELIABLE, with ``speed >= threshold``.

    Reliability masking is the point. An occluded wrist produces a one-frame
    position jump whose velocity spike is taller than any real swing
    (``contact.py:125-132``); an unreliable frame crossing the 10 % threshold
    would place the ready exit arbitrarily early.
    """
    values = np.asarray(speed, dtype=np.float64)
    flags = np.asarray(reliable, dtype=bool)
    if values.shape[0] == 0 or flags.shape != values.shape:
        return None
    candidates = np.flatnonzero(flags & np.isfinite(values) & (values >= float(threshold)))
    candidates = candidates[candidates >= max(int(start), 0)]
    if candidates.shape[0] == 0:
        return None
    return int(candidates[0])


def backward_extreme_frame(
    forward: np.ndarray, reliable: np.ndarray, first: int, last: int
) -> int | None:
    """Frame of maximum BACKWARD hand displacement in the inclusive ``[first, last]``.

    ``forward`` is ``points[:, wrist, 0] * swing_direction_sign``
    (``contact.py:548``), so the most backward frame is the minimum. Searched
    over reliable frames only, for the reason in
    :func:`first_reliable_frame_at_or_above`.
    """
    values = np.asarray(forward, dtype=np.float64)
    flags = np.asarray(reliable, dtype=bool)
    if values.shape[0] == 0 or flags.shape != values.shape:
        return None
    window = np.zeros(values.shape, dtype=bool)
    low = max(int(first), 0)
    high = min(int(last), values.shape[0] - 1)
    if high < low:
        return None
    window[low : high + 1] = True
    candidates = np.flatnonzero(window & flags & np.isfinite(values))
    if candidates.shape[0] == 0:
        return None
    return int(candidates[int(np.argmin(values[candidates]))])


def first_reliable_frame_below(
    speed: np.ndarray, reliable: np.ndarray, threshold: float, first: int, last: int
) -> int | None:
    """First reliable frame in ``[first, last]`` whose speed is below ``threshold``."""
    values = np.asarray(speed, dtype=np.float64)
    flags = np.asarray(reliable, dtype=bool)
    if values.shape[0] == 0 or flags.shape != values.shape:
        return None
    low = max(int(first), 0)
    high = min(int(last), values.shape[0] - 1)
    if high < low:
        return None
    window = np.zeros(values.shape, dtype=bool)
    window[low : high + 1] = True
    candidates = np.flatnonzero(
        window & flags & np.isfinite(values) & (values < float(threshold))
    )
    if candidates.shape[0] == 0:
        return None
    return int(candidates[0])


# --- boundary bookkeeping ----------------------------------------------------


def monotonic_bounds(starts: list[int], stop: int) -> list[int]:
    """Clamp five phase starts into ``[0, stop]`` and force them non-decreasing.

    Returns six boundaries: the five starts plus the EXCLUSIVE end of the last
    phase. Forcing monotonicity here is what makes ordering, contiguity and
    non-overlap structural rather than something each caller has to check.
    """
    limit = max(int(stop), 0)
    bounds = [0]
    for value in starts[1:]:
        clamped = int(np.clip(int(value), 0, limit))
        bounds.append(max(clamped, bounds[-1]))
    bounds.append(limit)
    bounds[-1] = max(bounds[-1], bounds[-2])
    return bounds


def enforce_minimum_lengths(
    bounds: list[int], *, min_frames: int = MIN_PHASE_FRAMES
) -> tuple[list[int], list[SwingPhaseName]]:
    """Collapse any phase shorter than ``min_frames`` to zero duration.

    PIPELINE.md:930. A phase of one frame is degenerate: it is not a window any
    range, minimum or standard deviation can be computed over. Its frame is
    handed to the NEXT phase (or, for the last phase, to the previous one --
    there is nowhere else for it to go and the phases must stay contiguous).

    Returns the adjusted boundaries and the names of the phases that ended up
    collapsed, recomputed from the FINAL boundaries rather than accumulated as
    we go: an earlier adjustment can un-collapse a later phase.
    """
    adjusted = list(bounds)
    for index in range(len(PHASE_ORDER) - 1):
        length = adjusted[index + 1] - adjusted[index]
        if 0 < length < int(min_frames):
            adjusted[index + 1] = adjusted[index]
    tail = len(PHASE_ORDER) - 1
    if 0 < adjusted[tail + 1] - adjusted[tail] < int(min_frames):
        adjusted[tail] = adjusted[tail + 1]
    collapsed = [
        name
        for index, name in enumerate(PHASE_ORDER)
        if adjusted[index + 1] - adjusted[index] <= 0
    ]
    return adjusted, collapsed


def build_phase_list(bounds: list[int], timestamps_s: np.ndarray) -> list[SwingPhase]:
    """Turn six boundaries into the five inclusive-bounded ``SwingPhase`` records.

    Durations come from ``timestamps_s`` differences, never from frame counts
    divided by 30: Stage 5 is variable-frame-rate (PIPELINE.md:165-166) and
    ``NormalizedSequence`` deliberately keeps full-precision PTS
    (``internal.py:52``).
    """
    stamps = np.asarray(timestamps_s, dtype=np.float64)
    count = int(stamps.shape[0])
    phases: list[SwingPhase] = []
    for index, name in enumerate(PHASE_ORDER):
        start = int(bounds[index])
        end = int(bounds[index + 1]) - 1
        anchor = float(stamps[int(np.clip(start, 0, count - 1))])
        if end < start:  # collapsed -- zero duration, both times at the anchor
            phases.append(
                SwingPhase(
                    name=name,
                    start_frame=start,
                    end_frame=end,
                    start_time_s=anchor,
                    end_time_s=anchor,
                    duration_s=0.0,
                )
            )
            continue
        end_time = float(stamps[int(np.clip(end, 0, count - 1))])
        phases.append(
            SwingPhase(
                name=name,
                start_frame=start,
                end_frame=end,
                start_time_s=anchor,
                end_time_s=end_time,
                duration_s=end_time - anchor,
            )
        )
    return phases


def tempo_ratio_from(phases: list[SwingPhase]) -> float | None:
    """``takeback_duration / forward_swing_duration`` (PIPELINE.md:930).

    ``None`` -- not ``0.0`` and not ``inf`` -- when the forward swing collapsed:
    a ratio with a zero denominator was not measured.
    """
    durations = {phase.name: float(phase.duration_s) for phase in phases}
    takeback = durations.get(SwingPhaseName.TAKEBACK)
    forward = durations.get(SwingPhaseName.FORWARD_SWING)
    if takeback is None or forward is None or forward <= 0.0:
        return None
    return takeback / forward


# --- orchestrator ------------------------------------------------------------


def segment_swing_phases(
    seq: NormalizedSequence,
    handedness: HandednessResult,
    contact: ContactDetection,
) -> tuple[SwingPhases | None, list[str]]:
    """Stage 12. NEVER raises. ``None`` means "not segmentable", never a guess.

    ``warnings`` is free text and carries everything ``SwingPhases`` has nowhere
    to hold (``responses.py:163-169``): why segmentation was refused, which
    phases collapsed, and whether the contact frame it was built around was
    itself confident.
    """
    warnings: list[str] = []
    try:
        points = np.asarray(seq.points, dtype=np.float64)
        stamps = np.asarray(seq.timestamps_s, dtype=np.float64)
        total = int(points.shape[0])

        if total < MIN_SEGMENTABLE_FRAMES or stamps.shape[0] < total:
            warnings.append(
                f"sequence too short to segment ({total} frames < "
                f"{MIN_SEGMENTABLE_FRAMES}); no phases"
            )
            return None, warnings

        if SEQUENCE_UNUSABLE in contact.sanity_flags:
            warnings.append(
                f"contact detection reported {SEQUENCE_UNUSABLE!r}; "
                "there is no contact frame to segment around"
            )
            return None, warnings

        hand = handedness.handedness
        if hand == Handedness.UNKNOWN:
            # Every boundary here is defined on the RACKET hand. Guessing one
            # would segment the wrong arm and look completely normal.
            warnings.append(
                "handedness is unknown, so the racket hand is unknown; "
                "swing phases cannot be located without it"
            )
            return None, warnings

        contact_frame = int(contact.frame_index)
        if not 0 <= contact_frame < total:
            warnings.append(
                f"contact frame {contact_frame} is outside the clip "
                f"(0..{total - 1}); no phases"
            )
            return None, warnings

        speed = racket_hand_speed(seq, hand)
        reliable = racket_wrist_reliable(seq, hand)
        if speed.shape[0] != total or reliable.shape[0] != total:
            warnings.append(
                "racket-hand speed and the frame count disagree; no phases"
            )
            return None, warnings
        if not bool(np.asarray(reliable, dtype=bool).any()):
            # Deliberately no fallback to an unmasked search. Stage 9 can afford
            # one because its sanity gates then speak up (contact.py:561-574);
            # Stage 12 has no equivalent gate and five metrics consume the
            # phases as fact.
            warnings.append(
                "the racket wrist was never reliably observed; "
                "the speed curve is not evidence of anything"
            )
            return None, warnings

        peak_frame = peak_speed_index(speed, reliable)
        peak_speed = float(speed[peak_frame])
        if not np.isfinite(peak_speed) or peak_speed <= 0.0:
            warnings.append(
                f"peak racket-hand speed is {peak_speed!r}; every "
                "fraction-of-peak phase boundary is undefined"
            )
            return None, warnings

        ready_exit = first_reliable_frame_at_or_above(
            speed, reliable, READY_EXIT_FRACTION * peak_speed
        )
        if ready_exit is None or ready_exit > contact_frame:
            warnings.append(
                "the hand never reaches "
                f"{READY_EXIT_FRACTION:.0%} of peak speed before contact; "
                "the takeback cannot be located"
            )
            return None, warnings

        wrist = racket_wrist_index(hand)
        forward = points[:, wrist, 0] * float(seq.swing_direction_sign)
        backward_extreme = backward_extreme_frame(
            forward, reliable, ready_exit, contact_frame
        )
        if backward_extreme is None:
            warnings.append(
                "no reliable frame between the ready exit and contact; "
                "the backward extreme cannot be located"
            )
            return None, warnings

        follow_start = contact_frame + CONTACT_HALF_WIDTH + 1
        follow_end = first_reliable_frame_below(
            speed,
            reliable,
            FOLLOW_EXIT_FRACTION * peak_speed,
            follow_start,
            total - 1,
        )
        if follow_end is None:
            warnings.append(
                "the hand never decelerates below "
                f"{FOLLOW_EXIT_FRACTION:.0%} of peak speed; the follow-through "
                "is reported to the end of the clip"
            )
        stop = total if follow_end is None else follow_end + 1

        bounds = monotonic_bounds(
            [
                0,
                ready_exit,
                backward_extreme + 1,
                contact_frame - CONTACT_HALF_WIDTH,
                contact_frame + CONTACT_HALF_WIDTH + 1,
            ],
            stop,
        )
        bounds, collapsed = enforce_minimum_lengths(bounds)
        if bounds[-1] <= bounds[0]:
            warnings.append(
                "every phase collapsed; the ordering invariant cannot be "
                "satisfied, so no phases are reported"
            )
            return None, warnings

        phase_list = build_phase_list(bounds, stamps)
        if collapsed:
            warnings.append(
                "phases shorter than "
                f"{MIN_PHASE_FRAMES} frames collapsed to zero duration: "
                + ", ".join(name.value for name in collapsed)
            )
        if float(contact.confidence) < LOW_CONTACT_CONFIDENCE:
            # PIPELINE.md:581 records an accepted +1 frame residual on the
            # contact frame. It is NOT corrected here -- subtracting 1 would
            # encode an unresolved measurement artefact as fact -- so the
            # ambiguity travels with the phases instead.
            warnings.append(
                f"contact confidence {float(contact.confidence):.2f} < "
                f"{LOW_CONTACT_CONFIDENCE}; every phase boundary is anchored to "
                "that frame and inherits its uncertainty"
            )

        result = SwingPhases(phases=phase_list, tempo_ratio=tempo_ratio_from(phase_list))
        return result, warnings
    except Exception as error:  # noqa: BLE001 -- Stage 12 contract: cannot raise.
        # Fixing the Stage 13 defect rather than copying it: an internal crash
        # is NAMED, so it is distinguishable from an honest "cannot segment".
        warnings.append(
            f"{INTERNAL_FAILURE_WARNING} ({type(error).__name__}: {error}); "
            "no phases were produced because of that failure, NOT because the "
            "clip was unsegmentable"
        )
        return None, warnings
