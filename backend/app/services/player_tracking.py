"""Player tracking for match mode (2026-11-22). IMPURE: decodes the video and
runs MediaPipe pose. The decisions are the pure ``analysis/player_track``.

The ball pass finds the hits first. This pass then looks only at the players
around those hits, because pose on every frame of a five-minute video would
roughly double the processing time for frames that carry no information:

1. **Where each player is.** The court is calibrated, so each half of it,
   with run-off and headroom for a tall player, projects to a box in the
   image (``plan_crops``). Pose runs on that crop of the full-resolution
   frame, so a far player is not lost to downscaling.
2. **When.** One sequential decode. Frames are converted and posed only
   inside a window around each ball hit (``-0.45 s`` to ``+0.25 s``, about
   20 poses per second); everything else is decoded and dropped.
3. **Who.** Pose detections whose feet are not on the crop's own side of the
   net are dropped (a spectator or the next court). What is left is that
   end's player.
4. **Which swing met the ball.** For each hit, each end's frames are judged
   by ``contact_candidate``; the wrist nearest the ball wins. That gives the
   hitter's end, feet and wrist, and with the player's handedness (learned
   from their swings) the stroke.

Doubles: each half crop runs one pose, so with two players in a half the
detector picks one; the wrist-at-the-ball test then rejects a mismatch rather
than naming the wrong hitter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import av
import numpy as np

from app.analysis.ball_events import FLIGHT_START_FLAG, BallEvent, PoseHit, hitter_ground_point
from app.analysis.court_calibration import CourtCalibration
from app.analysis.homography import image_scale_px_per_m, project_points_3d
from app.analysis.player_track import (
    CONTACT_WINDOW_S,
    Contact,
    Handedness,
    PlayerFrame,
    classify_stroke,
    contact_candidate,
    estimate_handedness,
    on_players_side,
    pose_hit_from_contact,
)
from app.models.court import BallEventKind, CourtEnd
from app.models.enums import ErrorCode
from app.pose.crop import CropBox, crop_pixels, uncrop_landmarks
from app.pose.extractor import PoseExtractor, resolve_model_path
from app.pose.video_io import (
    POSE_LONG_EDGE_PX,
    VideoDecodeError,
    _display_rotation,
    apply_rotation,
    resize_to_long_edge,
)

logger = logging.getLogger("tennisform.players")

#: Pose rate inside a window, per second.
POSE_FPS: Final[float] = 20.0
WINDOW_BEFORE_S: Final[float] = 0.45
WINDOW_AFTER_S: Final[float] = 0.25
#: At most this many ball hits are looked at; a longer match is sampled.
MAX_TRACKED_HITS: Final[int] = 160
#: Court metres a half's crop covers beyond the doubles lines and baseline,
#: and the height of a player with a raised racket.
RUNOFF_ACROSS_M: Final[float] = 3.0
RUNOFF_ALONG_M: Final[float] = 5.0
HEADROOM_M: Final[float] = 3.0
_DOUBLES_HALF_WIDTH_M: Final[float] = 5.485
_HALF_LENGTH_M: Final[float] = 11.885
#: Used when a half cannot be projected (a corner behind the camera).
_FALLBACK_CROPS: Final[dict[CourtEnd, CropBox]] = {
    CourtEnd.NEAR: (0.0, 0.35, 1.0, 1.0),
    CourtEnd.FAR: (0.0, 0.0, 1.0, 0.65),
}
#: A crop this small (fraction of the frame) is grown to it: a tighter crop
#: upscales noise.
_MIN_CROP_SPAN: Final[float] = 0.2


@dataclass(frozen=True)
class PlayerTrackingReport:
    """What the pass did, for the log and the tests."""

    hits_considered: int
    hits_matched: int
    frames_posed: int
    strokes_classified: int
    handedness: dict[str, str | None] = field(default_factory=dict)
    error: str | None = None


def plan_crops(calibration: CourtCalibration) -> dict[CourtEnd, CropBox]:
    """Each half's image box as fractions of the frame: the half court, its
    run-off and a player's height, projected through the calibration."""
    width, height = calibration.frame_size
    crops: dict[CourtEnd, CropBox] = {}
    for end, sign in ((CourtEnd.FAR, 1.0), (CourtEnd.NEAR, -1.0)):
        x = _DOUBLES_HALF_WIDTH_M + RUNOFF_ACROSS_M
        far_y = _HALF_LENGTH_M + RUNOFF_ALONG_M
        corners = np.array(
            [(sx * x, sign * y, z) for sx in (-1.0, 1.0) for y in (0.0, far_y) for z in (0.0, HEADROOM_M)],
            dtype=np.float64,
        )
        matrix = calibration.pose.projection_matrix
        depth = np.column_stack([corners, np.ones(len(corners))]) @ matrix.T
        if np.any(depth[:, 2] <= 1e-6):
            crops[end] = _FALLBACK_CROPS[end]  # a corner is behind the camera
            continue
        pixels = project_points_3d(calibration.pose, corners)
        x0, y0 = pixels.min(axis=0)
        x1, y1 = pixels.max(axis=0)
        box = (
            max(0.0, float(x0) / width),
            max(0.0, float(y0) / height),
            min(1.0, float(x1) / width),
            min(1.0, float(y1) / height),
        )
        if box[2] - box[0] < _MIN_CROP_SPAN or box[3] - box[1] < _MIN_CROP_SPAN:
            crops[end] = _FALLBACK_CROPS[end]
        else:
            crops[end] = box
    return crops


def windows_for(times_s: Sequence[float]) -> list[tuple[float, float]]:
    """Merged ``[t - before, t + after]`` windows, in time order."""
    spans = sorted((max(0.0, t - WINDOW_BEFORE_S), t + WINDOW_AFTER_S) for t in times_s)
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def hit_events(events: Sequence[BallEvent]) -> list[BallEvent]:
    """The ball hits worth a pose look, sampled evenly past the cap."""
    hits = [e for e in events if e.kind is BallEventKind.HIT]
    if len(hits) <= MAX_TRACKED_HITS:
        return hits
    step = len(hits) / MAX_TRACKED_HITS
    return [hits[int(i * step)] for i in range(MAX_TRACKED_HITS)]


def _ends_for(event: BallEvent) -> tuple[CourtEnd, ...]:
    """A hit with a known end needs only that half; otherwise both."""
    if event.end in (CourtEnd.NEAR, CourtEnd.FAR):
        return (event.end,)
    return (CourtEnd.NEAR, CourtEnd.FAR)


def _collect_frames(
    video: Path,
    calibration: CourtCalibration,
    crops: dict[CourtEnd, CropBox],
    wanted: Sequence[tuple[float, float, tuple[CourtEnd, ...]]],
    progress: Callable[[float], None] | None,
) -> dict[CourtEnd, list[PlayerFrame]]:
    """One sequential decode; pose only inside the windows, on each needed
    half's crop. Detections off their own side of the net are dropped."""
    frames: dict[CourtEnd, list[PlayerFrame]] = {CourtEnd.NEAR: [], CourtEnd.FAR: []}
    windows = [(start, end, ends) for start, end, ends in wanted]
    if not windows:
        return frames
    model = resolve_model_path()
    extractors: dict[CourtEnd, PoseExtractor] = {}
    spacing = 1.0 / POSE_FPS
    last_posed = -1.0
    try:
        container = av.open(str(video))
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    try:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "stream has no time base")
        first = windows[0][0]
        if first > 0.0:
            try:
                container.seek(int(first / time_base), stream=stream, backward=True, any_frame=False)
            except av.FFmpegError:
                container.seek(0)
        rotation: int | None = None
        index = 0
        last_end = windows[-1][1]
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            if rotation is None:
                rotation = _display_rotation(stream, container)
            time_s = float(frame.pts) * time_base
            if time_s > last_end:
                break
            while index < len(windows) and time_s > windows[index][1]:
                index += 1
            if index >= len(windows) or time_s < windows[index][0]:
                continue
            if time_s - last_posed < spacing * 0.9:
                continue
            last_posed = time_s
            ends = windows[index][2]
            rgb = apply_rotation(frame.to_ndarray(format="rgb24"), rotation)
            for end in ends:
                box = crops[end]
                crop = resize_to_long_edge(crop_pixels(rgb, box), POSE_LONG_EDGE_PX)
                if end not in extractors:
                    extractors[end] = PoseExtractor(model, num_poses=1)
                landmarks, _, detected = extractors[end].detect_frame(crop, int(round(time_s * 1000.0)))
                if not detected:
                    continue
                full = uncrop_landmarks(landmarks, box)
                feet = _feet_ground(calibration, full)
                if not on_players_side(end, feet):
                    continue
                frames[end].append(PlayerFrame(time_s=time_s, landmarks=full))
            if progress is not None:
                progress(min(1.0, (time_s - first) / max(last_end - first, 1e-6)))
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()
        for extractor in extractors.values():
            extractor.close()
    return frames


def _feet_ground(calibration: CourtCalibration, landmarks: np.ndarray) -> tuple[float, float] | None:
    from app.analysis.player_track import feet_point

    width, height = calibration.frame_size
    feet = feet_point(landmarks, (width, height))
    return None if feet is None else hitter_ground_point(calibration, feet)


def track_players(
    video: Path,
    calibration: CourtCalibration,
    events: Sequence[BallEvent],
    *,
    progress: Callable[[float], None] | None = None,
) -> tuple[tuple[PoseHit, ...], PlayerTrackingReport]:
    """Pose hits (hitter end, feet, wrist, stroke) for the ball's hits.

    Never raises a decode or pose error: a failure leaves the ball-only
    result, and the report says why.
    """
    hits = hit_events(events)
    if not hits:
        return (), PlayerTrackingReport(0, 0, 0, 0)
    size = calibration.frame_size
    try:
        crops = plan_crops(calibration)
        wanted = [
            (start, end, _ends_in(start, end, hits))
            for start, end in windows_for([h.time_s for h in hits])
        ]
        frames = _collect_frames(video, calibration, crops, wanted, progress)
    except Exception as exc:  # noqa: BLE001 - tracking is optional; the ball result stands
        logger.exception("player tracking failed")
        return (), PlayerTrackingReport(len(hits), 0, 0, 0, error=f"{type(exc).__name__}: {exc}")

    posed = sum(len(v) for v in frames.values())
    contacts: list[tuple[BallEvent, CourtEnd, Contact]] = []
    for event in hits:
        scale = image_scale_px_per_m(
            calibration.pose, calibration.h_image_to_court, event.image_xy_px
        )
        best: tuple[CourtEnd, Contact] | None = None
        for end in _ends_for(event):
            contact = contact_candidate(frames[end], event.time_s, event.image_xy_px, size, scale)
            if contact is not None and (best is None or contact.distance_px < best[1].distance_px):
                best = (end, contact)
        if best is not None:
            contacts.append((event, best[0], best[1]))

    handedness: dict[CourtEnd, Handedness | None] = {
        end: estimate_handedness([c.wrist for _, e, c in contacts if e is end])
        for end in (CourtEnd.NEAR, CourtEnd.FAR)
    }
    pose_hits: list[PoseHit] = []
    classified = 0
    for event, end, contact in contacts:
        scale = image_scale_px_per_m(
            calibration.pose, calibration.h_image_to_court, event.image_xy_px
        )
        base = pose_hit_from_contact(event.time_s, contact, end, None, _gate_px(scale))
        ground = (
            None
            if base.feet_xy_px is None
            else hitter_ground_point(calibration, base.feet_xy_px)
        )
        stroke = classify_stroke(
            contact,
            handedness[end],
            launch=FLIGHT_START_FLAG in event.flags,
            ground_along_m=None if ground is None else ground[1],
        )
        classified += stroke is not None
        pose_hits.append(
            PoseHit(
                time_s=base.time_s,
                wrist_xy_px=base.wrist_xy_px,
                end=base.end,
                confidence=base.confidence,
                shot_type=stroke,
                feet_xy_px=base.feet_xy_px,
            )
        )
    report = PlayerTrackingReport(
        hits_considered=len(hits),
        hits_matched=len(contacts),
        frames_posed=posed,
        strokes_classified=classified,
        handedness={end.value: handedness[end] for end in handedness},
    )
    return tuple(pose_hits), report


def _gate_px(scale_px_per_m: float) -> float:
    from app.analysis.ball_events import POSE_HIT_WRIST_GATE_M

    return POSE_HIT_WRIST_GATE_M * scale_px_per_m


def _ends_in(start: float, end: float, hits: Sequence[BallEvent]) -> tuple[CourtEnd, ...]:
    """The halves any hit inside ``[start, end]`` needs."""
    needed: set[CourtEnd] = set()
    for hit in hits:
        if start <= hit.time_s + WINDOW_BEFORE_S and hit.time_s - WINDOW_AFTER_S <= end:
            needed.update(_ends_for(hit))
    return tuple(e for e in (CourtEnd.NEAR, CourtEnd.FAR) if e in needed)


__all__ = [
    "CONTACT_WINDOW_S",
    "PlayerTrackingReport",
    "hit_events",
    "plan_crops",
    "track_players",
    "windows_for",
]
