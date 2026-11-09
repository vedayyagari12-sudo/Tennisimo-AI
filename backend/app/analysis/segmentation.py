"""Session Mode swing segmentation (SESSION_MODE.md section 4).

PURE: numpy plus the existing Stage 7-15 functions. No I/O, no clock, no model.

``contact.py``, ``phases.py`` and ``metrics.py`` assume ONE swing per input and
are not modified (4.1). This module finds every swing in a long sequence and
hands each one to the existing stack as a slice that looks like the short clips
it was built for, so single-swing mode is untouched by construction.

Steps (4.2):
1. A session speed signal: rolling torso scale, split into tracking segments at
   long gaps and identity breaks, smoothed and differentiated per segment only.
2. Candidate peaks with a refractory period, then floor and prominence filters.
3. One window per accepted peak; neighbours split at the minimum-speed frame,
   so windows never overlap.
4. Stages 7-9 re-run on each slice, handedness locked to the user's hint.
5. Every rejection has a reason from a closed enum, counted per session.
6. Confidence bands: accepted, low confidence, rejected.

ALL CONSTANTS ARE PLACEHOLDERS until the section 1 experiment calibrates them on
labelled real sessions. Synthetic tests prove the logic, not the accuracy.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.analysis.contact import (
    CONTACT_NOT_FOUND,
    MOTION_NOT_SUSTAINED,
    RACKET_WRIST_UNOBSERVED,
    candidate_peak_indices,
    detect_contact_frame,
    racket_hand_speed,
    racket_wrist_reliable,
)
from app.analysis.handedness import detect_handedness, racket_wrist_index
from app.analysis.metrics import compute_swing_metrics
from app.analysis.normalize import (
    CENTROID_JUMP_TU,
    SUBJECT_IDENTITY_UNSTABLE,
    TORSO_CHANGE_FRACTION,
    VISIBILITY_THRESHOLD,
    apply_aspect_correction,
    flip_y,
    mid_hip,
    normalize_sequence,
    origin_to_pixels,
    to_body_frame,
    torso_lengths,
    valid_frames,
)
from app.analysis.phases import SEQUENCE_UNUSABLE, segment_swing_phases
from app.analysis.scoring import build_scorecard
from app.analysis.shot_type import infer_shot_type
from app.analysis.smoothing import (
    MAX_GAP_FRAMES,
    central_difference,
    interpolate_gaps,
    savgol_smooth,
)
from app.models.enums import Handedness, HandednessSource
from app.models.internal import NormalizedSequence, PoseSequence
from app.models.responses import (
    ContactDetection,
    HandednessResult,
    PoseQuality,
    Scorecard,
    ShotTypeInference,
    SwingMetrics,
    SwingPhases,
)
from app.models.session import SwingRejection, SwingStatus

SEGMENTATION_VERSION: Final[str] = "seg_v1"

#: Half the 6.0 TU/s lower edge of the forehand topspin band, below the slice
#: band's 4.0. A peak under it is not a candidate at all, only counted.
SWING_FLOOR_TU_S: Final[float] = 3.0
#: A candidate under this share of the session's P90 candidate peak is
#: rejected ``below_relative_floor``. Uncalibrated.
RELATIVE_FLOOR_FRACTION: Final[float] = 0.35
RELATIVE_FLOOR_PERCENTILE: Final[float] = 90.0
#: Forward swing plus follow-through. Fast volley exchanges are the known cost.
REFRACTORY_S: Final[float] = 0.6
#: Peak must be at least this multiple of the HIGHER adjacent valley.
MIN_PEAK_VALLEY_RATIO: Final[float] = 2.0
#: Rolling torso scale: median of valid torso lengths over +/- this, in time.
ROLLING_SCALE_HALF_WINDOW_S: Final[float] = 5.0

WINDOW_BEFORE_S: Final[float] = 2.0
WINDOW_AFTER_S: Final[float] = 1.0
MIN_WINDOW_BEFORE_S: Final[float] = 0.5
MIN_WINDOW_AFTER_S: Final[float] = 0.2
#: The slice's winning speed peak further than this from the candidate peak
#: means Stage 9 keyed off a different event: rejected ``contact_off_peak``.
CONTACT_OFF_PEAK_FRAMES: Final[int] = 6

ACCEPT_CONFIDENCE: Final[float] = 0.45
LOW_CONFIDENCE_FLOOR: Final[float] = 0.35

#: Timestamp comparisons tolerate float noise in PTS.
_TIME_EPS_S: Final[float] = 1e-6
#: Landmark rounding for the diagnostic digest (4.4).
_DIGEST_DECIMALS: Final[int] = 4


@dataclass(frozen=True)
class SessionSignal:
    """Step 1 output: racket-hand speed over the whole session, in rolling TU/s.

    ``segments`` are ``[start, end)`` frame ranges of continuous tracking.
    Frames outside every segment have speed 0 and are not reliable.
    """

    speed: np.ndarray            # (T,) float64
    reliable: np.ndarray         # (T,) bool
    timestamps_s: np.ndarray     # (T,) float64
    rolling_scale: np.ndarray    # (T,) float64, aspect-corrected units per TU
    segments: tuple[tuple[int, int], ...]
    segment_of: np.ndarray       # (T,) int, -1 outside every segment


@dataclass(frozen=True)
class SwingCandidate:
    """Step 2: one speed peak at or above ``SWING_FLOOR_TU_S``."""

    peak_index: int
    peak_time_s: float
    peak_speed_tu_s: float
    segment: int
    valley_ratio: float | None   # None when both adjacent valleys are ~0
    rejection: SwingRejection | None


@dataclass(frozen=True)
class SwingWindow:
    """Step 3: the frames ``[start, end)`` that belong to one swing."""

    peak_index: int
    start: int
    end: int
    rejection: SwingRejection | None


@dataclass(frozen=True)
class SwingAnalysis:
    """Steps 4-6 for one window. ``contact`` is None if the slice never got there."""

    window: SwingWindow
    status: SwingStatus
    rejection: SwingRejection | None
    confidence: float | None
    contact: ContactDetection | None
    handedness: HandednessResult | None
    normalized: NormalizedSequence | None
    quality: PoseQuality | None
    start_time_s: float
    end_time_s: float


@dataclass(frozen=True)
class SwingScore:
    """Stages 12-15 on one accepted or low-confidence swing, as ``pipeline.py``."""

    phases: SwingPhases | None
    metrics: SwingMetrics
    shot_type: ShotTypeInference
    scorecard: Scorecard
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SegmentationResult:
    version: str
    signal: SessionSignal
    candidates: tuple[SwingCandidate, ...]
    swings: tuple[SwingAnalysis, ...]
    below_floor_count: int
    rejection_counts: dict[str, int]
    landmark_digest: str


# --------------------------------------------------------------------------
# Step 1: the session speed signal
# --------------------------------------------------------------------------


def rolling_torso_scale(
    lengths: np.ndarray,
    valid: np.ndarray,
    timestamps_s: np.ndarray,
    *,
    half_window_s: float = ROLLING_SCALE_HALF_WINDOW_S,
) -> np.ndarray:
    """Per-frame median of valid torso lengths within +/- ``half_window_s``.

    Time-based, not frame-based, so variable frame rate cannot change it.
    Frames with no valid length in reach take the nearest frame's value, so an
    interpolated frame inside a segment always has a scale. All zero only when
    no frame anywhere is valid.
    """
    values = np.asarray(lengths, dtype=np.float64)
    times = np.asarray(timestamps_s, dtype=np.float64)
    good = np.asarray(valid, dtype=bool) & (values > 0.0)
    out = np.zeros(values.shape, dtype=np.float64)
    if not good.any():
        return out
    good_times = times[good]
    good_values = values[good]
    lo = np.searchsorted(good_times, times - float(half_window_s), side="left")
    hi = np.searchsorted(good_times, times + float(half_window_s), side="right")
    for index in range(values.shape[0]):
        if hi[index] > lo[index]:
            out[index] = float(np.median(good_values[lo[index] : hi[index]]))
    known = np.flatnonzero(out > 0.0)
    if known.size < out.size:
        frames = np.arange(out.size, dtype=np.float64)
        out = np.interp(frames, known.astype(np.float64), out[known])
    return out


def tracking_segments(
    xy: np.ndarray,
    valid: np.ndarray,
    rolling_scale: np.ndarray,
    *,
    max_gap_frames: int = MAX_GAP_FRAMES,
    centroid_jump_tu: float = CENTROID_JUMP_TU,
    torso_change_fraction: float = TORSO_CHANGE_FRACTION,
) -> tuple[tuple[int, int], ...]:
    """``[start, end)`` runs of continuous tracking of one body.

    A run ends at an invalid stretch longer than ``max_gap_frames`` (nothing to
    interpolate across) or at an identity break between consecutive valid
    frames, judged with Stage 7's own thresholds in ROLLING TU. A session
    always contains some identity break; splitting there confines it to the
    swings next to it instead of penalising every swing (4.1).
    """
    flags = np.asarray(valid, dtype=bool)
    indices = np.flatnonzero(flags)
    if indices.size == 0:
        return ()
    data = np.asarray(xy, dtype=np.float64)
    centroids = mid_hip(data)
    lengths = torso_lengths(data)
    scale = np.asarray(rolling_scale, dtype=np.float64)

    segments: list[tuple[int, int]] = []
    start = int(indices[0])
    previous = int(indices[0])
    for current_raw in indices[1:]:
        current = int(current_raw)
        broken = current - previous - 1 > int(max_gap_frames)
        if not broken and scale[current] > 0.0:
            jump = float(np.linalg.norm(centroids[current] - centroids[previous]))
            if jump / float(scale[current]) > centroid_jump_tu:
                broken = True
            elif lengths[previous] > 0.0:
                change = abs(float(lengths[current] - lengths[previous]))
                broken = change / float(lengths[previous]) > torso_change_fraction
        if broken:
            segments.append((start, previous + 1))
            start = current
        previous = current
    segments.append((start, previous + 1))
    return tuple(segments)


def _hold_unseen_landmark(
    points: np.ndarray, visibility: np.ndarray, landmark: int
) -> np.ndarray:
    """Interpolate ONE landmark across frames where it was not seen.

    Stage 7's gate is the mean of 12 landmarks, which one occluded wrist cannot
    move. An unseen wrist position left in place becomes a spike, and smoothing
    spreads that spike into neighbouring frames that ARE marked reliable. A
    frame we could not see is not evidence of speed.
    """
    seen = np.asarray(visibility[:, landmark], dtype=np.float64) >= VISIBILITY_THRESHOLD
    if seen.all() or not seen.any():
        return points
    out = np.array(points, copy=True)
    frames = np.arange(out.shape[0], dtype=np.float64)
    for axis in range(out.shape[2]):
        out[:, landmark, axis] = np.interp(
            frames, frames[seen], out[seen, landmark, axis]
        )
    return out


def segment_normalized(
    seq: PoseSequence,
    corrected_xy: np.ndarray,
    valid: np.ndarray,
    rolling_scale: np.ndarray,
    start: int,
    end: int,
    handedness: Handedness,
) -> NormalizedSequence:
    """Stage 7 for one tracking segment, with a ROLLING torso scale.

    Smoothing and differentiation run inside the segment only, so they never
    cross a long gap or an identity break.
    """
    landmarks = np.asarray(seq.landmarks, dtype=np.float64)[start:end]
    times = np.asarray(seq.timestamps_s, dtype=np.float64)[start:end]
    flags = np.asarray(valid, dtype=bool)[start:end]
    visibility = landmarks[:, :, 3]

    filled, _, _ = interpolate_gaps(corrected_xy[start:end], flags)
    filled = _hold_unseen_landmark(filled, visibility, racket_wrist_index(handedness))
    flipped = flip_y(filled)
    origin = mid_hip(flipped)
    body = to_body_frame(flipped, origin)
    scale = np.asarray(rolling_scale, dtype=np.float64)[start:end]
    safe = np.where(scale > 0.0, scale, 1.0)
    scaled = body / safe[:, None, None]

    points = savgol_smooth(scaled)
    velocity = central_difference(points, times)
    acceleration = central_difference(velocity, times)
    return NormalizedSequence(
        points=points,
        velocity=velocity,
        acceleration=acceleration,
        timestamps_s=times,
        valid=flags,
        visibility=np.asarray(visibility, dtype=np.float64),
        swing_direction_sign=1,
        torso_scale_px=float(np.median(safe)) * float(seq.height_px),
        width_px=int(seq.width_px),
        height_px=int(seq.height_px),
        origin_px=origin_to_pixels(origin, seq.height_px),
    )


def session_speed_signal(seq: PoseSequence, handedness: Handedness) -> SessionSignal:
    """Step 1: racket-hand speed in rolling TU/s, per tracking segment."""
    landmarks = np.asarray(seq.landmarks, dtype=np.float64)
    count = int(landmarks.shape[0])
    times = np.asarray(seq.timestamps_s, dtype=np.float64)
    speed = np.zeros((count,), dtype=np.float64)
    reliable = np.zeros((count,), dtype=bool)
    segment_of = np.full((count,), -1, dtype=np.int64)
    if count == 0:
        return SessionSignal(speed, reliable, times, np.zeros((0,)), (), segment_of)

    valid = valid_frames(landmarks, seq.detected)
    corrected = apply_aspect_correction(landmarks[:, :, :2], seq.width_px, seq.height_px)
    scale = rolling_torso_scale(torso_lengths(corrected), valid, times)
    segments = tracking_segments(corrected, valid, scale)

    for number, (start, end) in enumerate(segments):
        part = segment_normalized(seq, corrected, valid, scale, start, end, handedness)
        speed[start:end] = racket_hand_speed(part, handedness)
        reliable[start:end] = racket_wrist_reliable(part, handedness) & valid[start:end]
        segment_of[start:end] = number
    return SessionSignal(speed, reliable, times, scale, segments, segment_of)


# --------------------------------------------------------------------------
# Step 2: candidates
# --------------------------------------------------------------------------


def refractory_frames(timestamps_s: np.ndarray, *, refractory_s: float = REFRACTORY_S) -> int:
    """``REFRACTORY_S`` in frames at the sequence's median frame interval."""
    times = np.asarray(timestamps_s, dtype=np.float64)
    if times.size < 2:
        return 1
    step = float(np.median(np.diff(times)))
    if step <= 0.0:
        return 1
    return max(int(round(float(refractory_s) / step)), 1)


def valley_ratio(
    speed: np.ndarray,
    peak: int,
    left_bound: int,
    right_bound: int,
) -> float | None:
    """Peak speed over the HIGHER of its two adjacent valleys.

    A valley is the minimum speed between the peak and its neighbour (another
    candidate, or the segment edge). ``None`` means both valleys are ~0, which
    is maximally prominent.
    """
    values = np.asarray(speed, dtype=np.float64)
    left = float(values[left_bound : peak + 1].min())
    right = float(values[peak : right_bound + 1].min())
    valley = max(left, right)
    if valley <= 1e-9:
        return None
    return float(values[peak]) / valley


def find_candidates(
    signal: SessionSignal,
    *,
    separation_frames: int,
    floor_tu_s: float = SWING_FLOOR_TU_S,
    relative_fraction: float = RELATIVE_FLOOR_FRACTION,
    min_valley_ratio: float = MIN_PEAK_VALLEY_RATIO,
) -> tuple[tuple[SwingCandidate, ...], int]:
    """Step 2. Returns (candidates in time order, count of peaks under the floor).

    Reuses ``candidate_peak_indices`` uncapped: its greedy non-maximum
    suppression, highest first with index tie-breaks, IS the refractory period.
    """
    speed = signal.speed
    times = signal.timestamps_s
    peaks: list[tuple[int, int]] = []
    below_floor = 0
    for number, (start, end) in enumerate(signal.segments):
        if end - start < 3:
            continue
        found = candidate_peak_indices(
            speed[start:end],
            signal.reliable[start:end],
            window_start=0,
            separation=separation_frames,
            max_candidates=end - start,
        )
        for local in sorted(found):
            index = start + int(local)
            if float(speed[index]) >= floor_tu_s:
                peaks.append((index, number))
            else:
                below_floor += 1

    if not peaks:
        return (), below_floor
    reliable_speeds = [float(speed[i]) for i, _ in peaks if bool(signal.reliable[i])]
    relative_floor = (
        float(relative_fraction)
        * float(np.percentile(reliable_speeds, RELATIVE_FLOOR_PERCENTILE))
        if reliable_speeds
        else 0.0
    )

    candidates: list[SwingCandidate] = []
    for position, (index, number) in enumerate(peaks):
        seg_start, seg_end = signal.segments[number]
        left_bound = seg_start
        if position > 0 and peaks[position - 1][1] == number:
            left_bound = peaks[position - 1][0]
        right_bound = seg_end - 1
        if position + 1 < len(peaks) and peaks[position + 1][1] == number:
            right_bound = peaks[position + 1][0]
        ratio = valley_ratio(speed, index, left_bound, right_bound)

        rejection: SwingRejection | None = None
        if not bool(signal.reliable[index]):
            rejection = SwingRejection.WRIST_UNOBSERVED
        elif float(speed[index]) < relative_floor:
            rejection = SwingRejection.BELOW_RELATIVE_FLOOR
        elif ratio is not None and ratio < min_valley_ratio:
            rejection = SwingRejection.NOT_PROMINENT
        candidates.append(
            SwingCandidate(
                peak_index=index,
                peak_time_s=float(times[index]),
                peak_speed_tu_s=float(speed[index]),
                segment=number,
                valley_ratio=ratio,
                rejection=rejection,
            )
        )
    return tuple(candidates), below_floor


# --------------------------------------------------------------------------
# Step 3: windows
# --------------------------------------------------------------------------


def _split_frame(
    times: np.ndarray,
    speed: np.ndarray,
    left_peak: int,
    right_peak: int,
    *,
    min_before_s: float,
    min_after_s: float,
) -> int:
    """The minimum-speed frame between two accepted peaks, among the frames
    that leave BOTH swings their minimum window.

    DEVIATION from SESSION_MODE.md 4.2 step 3, which takes the plain minimum.
    On swings about a second apart the plain minimum is the second swing's
    takeback turn -- the wrist stops before it drives -- so the split lands
    just before the drive and the second swing is left with no build-up and
    rejected ``window_too_short``. Restricting the search keeps the rule
    (slowest frame, so no overlap) without that failure. When the peaks are
    too close for both minimums, the plain minimum is used and one window is
    rejected as too short, as 4.2 accepts.
    """
    lo = int(np.searchsorted(times, float(times[left_peak]) + min_after_s - _TIME_EPS_S))
    hi = int(np.searchsorted(times, float(times[right_peak]) - min_before_s + _TIME_EPS_S,
                             side="right"))
    # The split frame STARTS the right window, so the left window keeps the
    # frames before it: it needs the frame at +min_after, hence the + 1.
    lo = max(lo + 1, left_peak + 1)
    hi = min(hi, right_peak)
    if hi <= lo:
        lo, hi = left_peak + 1, right_peak
    if hi <= lo:
        return right_peak
    return lo + int(np.argmin(speed[lo:hi]))


def build_windows(
    signal: SessionSignal,
    candidates: tuple[SwingCandidate, ...],
    *,
    before_s: float = WINDOW_BEFORE_S,
    after_s: float = WINDOW_AFTER_S,
    min_before_s: float = MIN_WINDOW_BEFORE_S,
    min_after_s: float = MIN_WINDOW_AFTER_S,
) -> tuple[SwingWindow, ...]:
    """Step 3: one window per ACCEPTED candidate. Windows never overlap.

    Each window is the peak -``before_s`` to +``after_s``, clipped to its
    tracking segment and, between two accepted neighbours, split at the
    minimum-speed frame between them (see ``_split_frame``). The split frame starts the later window,
    so each frame belongs to at most one swing and a faster neighbour's peak
    can never enter a slice.
    """
    accepted = sorted(
        (c for c in candidates if c.rejection is None), key=lambda c: c.peak_index
    )
    times = signal.timestamps_s
    speed = signal.speed
    splits: list[int | None] = []
    for left, right in zip(accepted, accepted[1:]):
        if left.segment != right.segment:
            splits.append(None)
            continue
        splits.append(_split_frame(times, speed, left.peak_index, right.peak_index,
                                   min_before_s=min_before_s, min_after_s=min_after_s))

    windows: list[SwingWindow] = []
    for position, candidate in enumerate(accepted):
        peak = candidate.peak_index
        seg_start, seg_end = signal.segments[candidate.segment]
        peak_time = float(times[peak])
        start = int(np.searchsorted(times, peak_time - before_s - _TIME_EPS_S, side="left"))
        end = int(np.searchsorted(times, peak_time + after_s + _TIME_EPS_S, side="right"))
        start = max(start, seg_start)
        end = min(end, seg_end)
        if position > 0 and splits[position - 1] is not None:
            start = max(start, int(splits[position - 1]))  # type: ignore[arg-type]
        if position < len(splits) and splits[position] is not None:
            end = min(end, int(splits[position]))  # type: ignore[arg-type]

        rejection: SwingRejection | None = None
        if (
            end <= peak
            or peak_time - float(times[start]) < min_before_s - _TIME_EPS_S
            or float(times[end - 1]) - peak_time < min_after_s - _TIME_EPS_S
        ):
            rejection = SwingRejection.WINDOW_TOO_SHORT
        windows.append(SwingWindow(peak_index=peak, start=start, end=end, rejection=rejection))
    return tuple(windows)


# --------------------------------------------------------------------------
# Steps 4-6: per-window analysis
# --------------------------------------------------------------------------


def slice_pose_sequence(seq: PoseSequence, start: int, end: int) -> PoseSequence:
    """Frames ``[start, end)`` as their own ``PoseSequence``, timestamps intact."""
    if not 0 <= start < end <= int(np.asarray(seq.timestamps_s).shape[0]):
        raise ValueError("slice must be a non-empty range inside the sequence")
    return PoseSequence(
        landmarks=np.ascontiguousarray(seq.landmarks[start:end]),
        world=np.ascontiguousarray(seq.world[start:end]),
        timestamps_s=np.ascontiguousarray(seq.timestamps_s[start:end]),
        detected=np.ascontiguousarray(seq.detected[start:end]),
        width_px=seq.width_px,
        height_px=seq.height_px,
    )


def lock_handedness(detected: HandednessResult, hint: Handedness) -> HandednessResult:
    """Session handedness is the user's hint on every swing (4.2 step 4.3).

    Re-detecting per swing could flip hands between swings, and detection
    already fails on serves. The detection is kept only as a warning.
    """
    warnings = list(detected.warnings)
    racket_path = detected.racket_hand_path_length_tu
    off_path = detected.off_hand_path_length_tu
    if detected.handedness != hint:
        warnings.append(
            f"detection said {detected.handedness.value}; session hint "
            f"{hint.value} kept"
        )
        racket_path, off_path = off_path, racket_path
    return HandednessResult(
        handedness=hint,
        confidence=1.0,
        source=HandednessSource.USER_HINT,
        racket_hand_path_length_tu=racket_path,
        off_hand_path_length_tu=off_path,
        warnings=warnings,
    )


def _rejection_from_contact(
    contact: ContactDetection, window: SwingWindow
) -> SwingRejection | None:
    flags = set(contact.sanity_flags)
    if SEQUENCE_UNUSABLE in flags:
        return SwingRejection.SEQUENCE_UNUSABLE
    if CONTACT_NOT_FOUND in flags:
        return SwingRejection.CONTACT_NOT_FOUND
    if RACKET_WRIST_UNOBSERVED in flags:
        return SwingRejection.WRIST_UNOBSERVED
    if MOTION_NOT_SUSTAINED in flags:
        return SwingRejection.NO_MOTION_EVIDENCE
    # The slice's WINNING peak against the candidate peak, not the contact
    # frame: Stage 9 walks a speed plateau to its deceleration onset, so on a
    # swing whose drive hesitates the contact frame sits legitimately past the
    # peak. What this rejects is the slice keying off a different event.
    if abs(window.start + contact.peak_frame_index - window.peak_index) > CONTACT_OFF_PEAK_FRAMES:
        return SwingRejection.CONTACT_OFF_PEAK
    return None


def status_for_confidence(confidence: float) -> SwingStatus:
    if confidence >= ACCEPT_CONFIDENCE:
        return SwingStatus.ACCEPTED
    if confidence >= LOW_CONFIDENCE_FLOOR:
        return SwingStatus.LOW_CONFIDENCE
    return SwingStatus.REJECTED


def analyse_window(
    seq: PoseSequence,
    window: SwingWindow,
    hint: Handedness,
    *,
    source_start_s: float = 0.0,
) -> SwingAnalysis:
    """Steps 4-6: Stages 7-9 on one slice. Never raises on bad data.

    ``source_start_s`` is the PTS of the session sequence's time zero in the
    source file, so ``contact.contact_absolute_time_s`` is a source-file PTS.
    """
    times = np.asarray(seq.timestamps_s, dtype=np.float64)
    start_time = float(times[window.start])
    end_time = float(times[max(window.end - 1, window.start)])

    def rejected(
        reason: SwingRejection,
        *,
        contact: ContactDetection | None = None,
        handedness: HandednessResult | None = None,
        normalized: NormalizedSequence | None = None,
        quality: PoseQuality | None = None,
    ) -> SwingAnalysis:
        return SwingAnalysis(
            window=window,
            status=SwingStatus.REJECTED,
            rejection=reason,
            confidence=None if contact is None else contact.confidence,
            contact=contact,
            handedness=handedness,
            normalized=normalized,
            quality=quality,
            start_time_s=start_time,
            end_time_s=end_time,
        )

    if window.rejection is not None:
        return rejected(window.rejection)

    piece = slice_pose_sequence(seq, window.start, window.end)
    normalized, quality = normalize_sequence(piece)
    if not quality.usable:
        return rejected(SwingRejection.POSE_UNUSABLE, normalized=normalized, quality=quality)
    if SUBJECT_IDENTITY_UNSTABLE in quality.flags:
        return rejected(
            SwingRejection.IDENTITY_BREAK_IN_WINDOW, normalized=normalized, quality=quality
        )

    handedness = lock_handedness(detect_handedness(normalized, hint), hint)
    contact = detect_contact_frame(
        normalized,
        handedness,
        quality,
        analysis_window_start_s=float(source_start_s) + start_time,
    )
    reason = _rejection_from_contact(contact, window)
    status = status_for_confidence(contact.confidence)
    if reason is None and status is SwingStatus.REJECTED:
        reason = SwingRejection.CONFIDENCE_BELOW_FLOOR
    if reason is not None:
        return rejected(
            reason,
            contact=contact,
            handedness=handedness,
            normalized=normalized,
            quality=quality,
        )
    return SwingAnalysis(
        window=window,
        status=status,
        rejection=None,
        confidence=contact.confidence,
        contact=contact,
        handedness=handedness,
        normalized=normalized,
        quality=quality,
        start_time_s=start_time,
        end_time_s=end_time,
    )


def score_swing(swing: SwingAnalysis) -> SwingScore | None:
    """Stages 12-15 on one swing, exactly as ``pipeline.py`` runs them.

    None for a rejected swing: it is never scored.
    """
    if (
        swing.status is SwingStatus.REJECTED
        or swing.contact is None
        or swing.normalized is None
        or swing.handedness is None
    ):
        return None
    seq, handedness, contact = swing.normalized, swing.handedness, swing.contact
    phases, phase_warnings = segment_swing_phases(seq, handedness, contact)
    metrics, metric_warnings = compute_swing_metrics(seq, handedness, contact, phases)
    shot_type = infer_shot_type(metrics, seq, handedness, contact, phases)
    scorecard = build_scorecard(metrics, shot_type.shot_type)
    return SwingScore(
        phases=phases,
        metrics=metrics,
        shot_type=shot_type,
        scorecard=scorecard,
        warnings=tuple(phase_warnings) + tuple(metric_warnings),
    )


def landmark_digest(seq: PoseSequence) -> str:
    """SHA-256 of rounded landmarks and timestamps, for diagnosing retries (4.4).

    MediaPipe is not bit-reproducible across CPUs, so a retried job may differ;
    this says whether the INPUT differed, after which the landmarks are dropped.
    """
    digest = hashlib.sha256()
    digest.update(
        np.round(np.asarray(seq.landmarks, dtype=np.float64), _DIGEST_DECIMALS).tobytes()
    )
    digest.update(
        np.round(np.asarray(seq.timestamps_s, dtype=np.float64), _DIGEST_DECIMALS).tobytes()
    )
    digest.update(np.asarray(seq.detected, dtype=bool).tobytes())
    return digest.hexdigest()


def segment_session(
    seq: PoseSequence,
    hint: Handedness,
    *,
    source_start_s: float = 0.0,
) -> SegmentationResult:
    """Steps 1-6 over a whole session. Deterministic: same input, same output.

    ``hint`` is required: Session Mode locks handedness to the user's choice.
    """
    if hint not in (Handedness.RIGHT, Handedness.LEFT):
        raise ValueError("session segmentation requires a right or left handedness hint")
    signal = session_speed_signal(seq, hint)
    candidates, below_floor = find_candidates(
        signal, separation_frames=refractory_frames(signal.timestamps_s)
    )
    windows = build_windows(signal, candidates)
    swings = tuple(
        analyse_window(seq, window, hint, source_start_s=source_start_s) for window in windows
    )
    counts: Counter[str] = Counter()
    for candidate in candidates:
        if candidate.rejection is not None:
            counts[candidate.rejection.value] += 1
    for swing in swings:
        if swing.rejection is not None:
            counts[swing.rejection.value] += 1
    return SegmentationResult(
        version=SEGMENTATION_VERSION,
        signal=signal,
        candidates=candidates,
        swings=swings,
        below_floor_count=below_floor,
        rejection_counts=dict(sorted(counts.items())),
        landmark_digest=landmark_digest(seq),
    )


__all__ = [
    "ACCEPT_CONFIDENCE",
    "LOW_CONFIDENCE_FLOOR",
    "SEGMENTATION_VERSION",
    "SWING_FLOOR_TU_S",
    "SegmentationResult",
    "SessionSignal",
    "SwingAnalysis",
    "SwingCandidate",
    "SwingScore",
    "SwingWindow",
    "analyse_window",
    "build_windows",
    "find_candidates",
    "landmark_digest",
    "lock_handedness",
    "refractory_frames",
    "rolling_torso_scale",
    "score_swing",
    "segment_session",
    "session_speed_signal",
    "slice_pose_sequence",
    "status_for_confidence",
    "tracking_segments",
    "valley_ratio",
]
