"""Stage 10's impure half: the second decode pass that feeds the ball detector.

IMPURE: PyAV seeks and decodes, cv2 resizes, and this module reads the wall
clock to enforce the 6 s deadline. Every DECISION stays in the pure functions --
``detector.detect_ball_track`` and ``speed.compute_ball_speed``; this module only
acquires frames and stops the clock.

TWO THINGS THAT ARE EASY TO GET WRONG AND SILENT WHEN YOU DO
------------------------------------------------------------
**1. Seek off ``contact_absolute_time_s``, never ``time_s``.**
``ContactDetection.time_s`` is measured from ``analysis_window_start_s``
(``responses.py:72``); ``contact_absolute_time_s`` is the absolute PTS in the
source file, computed once at ``contact.py:528-532`` for exactly this seek. On a
60 s clip with an 8 s window they differ by the window offset, and using the
wrong one measures a perfectly valid 250 ms of the wrong part of the clip.

**2. These frames are BGR.** ``detect_ball_track`` documents its input as BGR
(``detector.py:349``) and runs ``COLOR_BGR2GRAY`` / ``COLOR_BGR2HSV`` on it. The
pose stream is RGB for MediaPipe. Handing the pose stream's array here does not
raise -- it rotates the hue channel of the colour mask that finds the yellow
ball, and the result is reported as ``too_few_detections``, indistinguishable
from a hard clip. PyAV is asked for ``bgr24`` directly; nothing in this pipeline
ever converts one stream's buffer into the other's convention.

Ball detection also decodes at **native fps**, not the pose stream's resampled
30 fps: Stage 5 selects the nearest frame per 1/30 s slot, so a 24 fps source
yields duplicate frames whose zero displacement drags the speed median straight
down (PIPELINE.md 10.2).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import av
import numpy as np
import numpy.typing as npt

from app.ball.detector import detect_ball_track, torso_leg_exclusion_box
from app.ball.geometry import (
    TORSO_LEG_LANDMARKS,
    landmarks_to_cal_px,
    racket_wrist_seed_px,
    seed_gate_px,
)
from app.ball.track import BallDetectionResult
from app.models.enums import BallSpeedUnavailableReason
from app.models.internal import NormalizedSequence
from app.models.responses import BallDetectionSummary
from app.pose.video_io import apply_rotation, resize_to_long_edge

#: 11.2 / speed.py:55 -- the measurement window after contact.
WINDOW_S: Final[float] = 0.25

#: 10.3 -- pre-roll used for the fixed temporal median background, and the gap
#: left between it and the window so the swing itself is not in the background.
BACKGROUND_PREROLL_S: Final[float] = 0.50
BACKGROUND_GAP_S: Final[float] = 0.05
BACKGROUND_MAX_FRAMES: Final[int] = 15

#: PIPELINE.md:594 -- hard wall-clock deadline for the whole stage.
DETECTION_DEADLINE_S: Final[float] = 6.0

DETECTOR_VERSION: Final[str] = "ball_cv_v1"


class DetectionDeadlineExceeded(Exception):
    """The 6 s wall-clock budget ran out. Never fails the job (PIPELINE.md:594)."""


@dataclass(frozen=True)
class MeasurementWindow:
    """The CAL_SPACE frame stack Stage 10 measures, plus its real timestamps.

    ``frames_bgr`` is ``(T, H, W, 3)`` uint8 **BGR** in CAL_SPACE, at the
    source's NATIVE rate. ``timestamps_s`` is one ABSOLUTE PTS per frame, which
    is what ``compute_ball_speed`` indexes with ``track.frame_offsets`` and what
    makes every step normalized by its own dt.
    """

    frames_bgr: npt.NDArray[np.uint8]
    timestamps_s: npt.NDArray[np.float64]
    background_bgr: npt.NDArray[np.uint8] | None
    native_fps: float
    window_start_s: float
    window_end_s: float


def _native_fps(timestamps_s: npt.NDArray[np.float64]) -> float:
    """Measured fps from PTS deltas, 0.0 when fewer than two frames.

    From the MEDIAN delta, not the mean: one long gap at a seek boundary must
    not redefine the clip's frame rate.
    """
    if timestamps_s.size < 2:
        return 0.0
    deltas = np.diff(timestamps_s)
    deltas = deltas[np.isfinite(deltas) & (deltas > 0.0)]
    if deltas.size == 0:
        return 0.0
    return float(1.0 / float(np.median(deltas)))


def load_measurement_window_bgr(
    path: Path,
    *,
    contact_absolute_time_s: float,
    cal_width_px: int,
    cal_height_px: int,
    rotation_deg: int,
    window_s: float = WINDOW_S,
    preroll_s: float = BACKGROUND_PREROLL_S,
    preroll_gap_s: float = BACKGROUND_GAP_S,
    background_max_frames: int = BACKGROUND_MAX_FRAMES,
    deadline_s: float = DETECTION_DEADLINE_S,
) -> MeasurementWindow:
    """Decode ``[contact, contact + window_s]`` at native fps into CAL_SPACE BGR.

    The argument is named ``contact_absolute_time_s`` and not ``contact_time_s``
    on purpose -- see the module docstring. Also decodes a pre-roll ending
    ``preroll_gap_s`` before contact for the background median, so the swing
    itself is not baked into the background it is detected against.

    Raises:
        DetectionDeadlineExceeded: if the wall clock passes ``deadline_s``.
        av.FFmpegError: the caller converts a decode failure into a null speed;
            Stage 10 never fails the job.
    """
    started = time.monotonic()
    contact_s = float(contact_absolute_time_s)
    window_end_s = contact_s + float(window_s)
    background_end_s = contact_s - float(preroll_gap_s)
    background_start_s = background_end_s - float(preroll_s)
    seek_target_s = max(0.0, background_start_s)

    long_edge_px = max(int(cal_width_px), int(cal_height_px))

    window_frames: list[np.ndarray] = []
    window_times: list[float] = []
    background_frames: list[np.ndarray] = []

    container = av.open(str(path))
    try:
        stream = container.streams.video[0]
        # Frame-threaded decode, matching the pose stream (app/pose/video_io.py).
        # Bit-identical output; it only lets the decoder use more than one core,
        # which matters here because this pass runs under a wall-clock deadline.
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            raise av.FFmpegError(0, "stream has no time base")

        if seek_target_s > 0.0:
            container.seek(
                int(seek_target_s / time_base), stream=stream, backward=True, any_frame=False
            )

        for frame in container.decode(stream):
            if time.monotonic() - started > deadline_s:
                raise DetectionDeadlineExceeded(
                    f"ball frame acquisition exceeded {deadline_s:.1f}s"
                )
            if frame.pts is None:
                continue
            absolute_pts_s = float(frame.pts) * time_base
            if absolute_pts_s > window_end_s:
                break
            in_background = background_start_s <= absolute_pts_s < background_end_s
            in_window = contact_s <= absolute_pts_s <= window_end_s
            if not (in_background or in_window):
                continue

            # PyAV converts to BGR. Never the pose stream's RGB buffer.
            bgr = frame.to_ndarray(format="bgr24")
            bgr = resize_to_long_edge(apply_rotation(bgr, rotation_deg), long_edge_px)
            if in_window:
                window_frames.append(bgr)
                window_times.append(absolute_pts_s)
            elif len(background_frames) < background_max_frames:
                background_frames.append(bgr)
    finally:
        container.close()

    frames = (
        np.ascontiguousarray(np.stack(window_frames), dtype=np.uint8)
        if window_frames
        else np.zeros((0, int(cal_height_px), int(cal_width_px), 3), dtype=np.uint8)
    )
    times = np.asarray(window_times, dtype=np.float64)
    background = (
        np.ascontiguousarray(np.stack(background_frames), dtype=np.uint8)
        if background_frames
        else None
    )
    if background is not None and frames.shape[0] and background.shape[1:] != frames.shape[1:]:
        # A mid-clip resolution change would make the median meaningless.
        background = None

    return MeasurementWindow(
        frames_bgr=frames,
        timestamps_s=times,
        background_bgr=background,
        native_fps=_native_fps(times),
        window_start_s=float(times[0]) if times.size else contact_s,
        window_end_s=float(times[-1]) if times.size else contact_s,
    )


def exclusion_boxes_for(
    seq: NormalizedSequence,
    window_timestamps_s: npt.NDArray[np.float64],
    *,
    cal_scale: float,
) -> list[tuple[float, float, float, float] | None]:
    """One dilated torso+leg box per measurement-window frame, in CAL_SPACE.

    The ball stream runs at native fps and the pose stream at 30 fps, so each
    ball frame takes the box of the NEAREST pose frame by absolute PTS. A
    ``None`` entry means no box for that frame -- honest when no pose frame is
    close enough or the landmarks were never seen -- and ``detect_ball_track``
    accepts it per-frame.
    """
    pose_times = np.asarray(seq.timestamps_s, dtype=np.float64)
    boxes: list[tuple[float, float, float, float] | None] = []
    for timestamp in np.asarray(window_timestamps_s, dtype=np.float64):
        if pose_times.size == 0:
            boxes.append(None)
            continue
        index = int(np.argmin(np.abs(pose_times - float(timestamp))))
        try:
            points = landmarks_to_cal_px(
                seq, index, TORSO_LEG_LANDMARKS, cal_scale=cal_scale
            )
        except IndexError:
            boxes.append(None)
            continue
        if not np.all(np.isfinite(points)):
            boxes.append(None)
            continue
        boxes.append(torso_leg_exclusion_box(points))
    return boxes


def run_ball_detection(
    path: Path,
    seq: NormalizedSequence,
    *,
    contact_absolute_time_s: float,
    racket_wrist_index: int,
    contact_frame_index: int,
    cal_width_px: int,
    cal_height_px: int,
    pose_long_edge_px: int,
    rotation_deg: int,
    px_per_m: float,
    deadline_s: float = DETECTION_DEADLINE_S,
) -> tuple[BallDetectionResult, BallDetectionSummary, npt.NDArray[np.float64]]:
    """Stage 10 end to end: acquire frames, build the hints, call the detector.

    NEVER raises. Every failure becomes ``BallDetectionResult(None, reason)``
    plus a summary recording what was actually scanned -- PIPELINE.md:594's
    "never fails the job", enforced here rather than hoped for upstream.

    Returns ``(result, summary, window_timestamps_s)``. The timestamps are
    returned separately because ``compute_ball_speed`` needs one absolute PTS
    per frame in the stack ``track.frame_offsets`` indexes, and ``BallTrack``
    deliberately carries no temporal fields.
    """
    started = time.monotonic()
    cal_long_edge_px = max(int(cal_width_px), int(cal_height_px))
    cal_scale = float(cal_long_edge_px) / float(pose_long_edge_px)
    empty_times = np.zeros((0,), dtype=np.float64)

    def summarize(
        window: MeasurementWindow | None,
        *,
        raw_candidates: int = 0,
        accepted: int = 0,
        round_tier: int = 0,
        streak_tier: int = 0,
        coasted: int = 0,
        terminated_by: str = "window_end",
    ) -> BallDetectionSummary:
        return BallDetectionSummary(
            frames_scanned=0 if window is None else int(window.frames_bgr.shape[0]),
            native_fps=0.0 if window is None else window.native_fps,
            detection_long_edge_px=cal_long_edge_px,
            raw_candidates=raw_candidates,
            accepted_detections=accepted,
            round_tier_detections=round_tier,
            streak_tier_detections=streak_tier,
            coasted_frames=coasted,
            window_start_s=(
                float(contact_absolute_time_s) if window is None else window.window_start_s
            ),
            window_end_s=(
                float(contact_absolute_time_s) if window is None else window.window_end_s
            ),
            terminated_by=terminated_by,
            detector_version=DETECTOR_VERSION,
            elapsed_ms=int((time.monotonic() - started) * 1000.0),
        )

    try:
        window = load_measurement_window_bgr(
            path,
            contact_absolute_time_s=contact_absolute_time_s,
            cal_width_px=cal_width_px,
            cal_height_px=cal_height_px,
            rotation_deg=rotation_deg,
            deadline_s=deadline_s,
        )
    except DetectionDeadlineExceeded:
        return (
            BallDetectionResult(None, BallSpeedUnavailableReason.DETECTION_TIMEOUT),
            summarize(None, terminated_by="miss_limit"),
            empty_times,
        )
    except Exception:  # noqa: BLE001 - Stage 10 never fails the job
        return (
            BallDetectionResult(None, BallSpeedUnavailableReason.NO_TRACK_SEEDED),
            summarize(None),
            empty_times,
        )

    if window.frames_bgr.shape[0] == 0:
        return (
            BallDetectionResult(None, BallSpeedUnavailableReason.TOO_FEW_DETECTIONS),
            summarize(window),
            window.timestamps_s,
        )

    try:
        seed = racket_wrist_seed_px(
            seq, contact_frame_index, racket_wrist_index, cal_scale=cal_scale
        )
        if not all(np.isfinite(seed)):
            seed = None
    except IndexError:
        seed = None

    boxes = exclusion_boxes_for(seq, window.timestamps_s, cal_scale=cal_scale)

    if time.monotonic() - started > deadline_s:
        return (
            BallDetectionResult(None, BallSpeedUnavailableReason.DETECTION_TIMEOUT),
            summarize(window, terminated_by="miss_limit"),
            window.timestamps_s,
        )

    try:
        result = detect_ball_track(
            window.frames_bgr,
            background_frames=window.background_bgr,
            exclusion_boxes=boxes,
            fps=window.native_fps or 30.0,
            px_per_m=float(px_per_m),
            seed_xy=seed,
            seed_gate_px=seed_gate_px(seq, cal_scale=cal_scale),
        )
    except Exception:  # noqa: BLE001 - Stage 10 never fails the job
        return (
            BallDetectionResult(None, BallSpeedUnavailableReason.NO_TRACK_SEEDED),
            summarize(window),
            window.timestamps_s,
        )

    track = result.track
    if track is None:
        return result, summarize(window), window.timestamps_s

    tiers = np.asarray(track.tier, dtype=np.int64)
    summary = summarize(
        window,
        raw_candidates=int(tiers.size),
        accepted=int(tiers.size),
        round_tier=int(np.count_nonzero(tiers == 1)),
        streak_tier=int(np.count_nonzero(tiers == 2)),
        coasted=int(track.coasted_frames),
        terminated_by=str(track.terminated_by),
    )
    return result, summary, window.timestamps_s


__all__: Sequence[str] = (
    "BACKGROUND_GAP_S",
    "BACKGROUND_MAX_FRAMES",
    "BACKGROUND_PREROLL_S",
    "DETECTION_DEADLINE_S",
    "DETECTOR_VERSION",
    "WINDOW_S",
    "DetectionDeadlineExceeded",
    "MeasurementWindow",
    "exclusion_boxes_for",
    "load_measurement_window_bgr",
    "run_ball_detection",
)
