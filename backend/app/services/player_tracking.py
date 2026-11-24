"""Player tracking for match mode (2026-11-22). IMPURE: decodes the video and
runs MediaPipe pose. The decisions are the pure ``analysis/player_track``.

The ball pass finds the hits first. This pass then looks at the player around
each one, because pose on every frame of a five-minute video would roughly
double the processing time for frames that carry no information.

1. **Where.** At a hit the hitter is within a couple of metres of the ball,
   and the ball's place in the picture is known. So each hit gets a small crop
   of the full-resolution frame around the ball (``hit_crop``): the player is
   large in it, however far from the camera, which a crop of a whole half
   court is not (a far player is a few dozen pixels in that).
2. **When.** One sequential decode. A frame is converted and posed only inside
   a hit's window (-0.35 s to +0.2 s, about 15 poses a second); everything
   else is decoded and dropped. A fresh pose model per window, so its own
   frame-to-frame tracking never carries across two different crops.
3. **Which swing met the ball, and who.** ``contact_candidate`` takes the wrist
   trajectory in the window: it must swing, and be at the ball. The hitter's
   end is the half of the court their FEET stand on (the homography maps feet,
   which are on the ground); with no feet visible the ball event's own end is
   used, and with neither there is no result.
4. **Stroke.** With the player's handedness (learned from their swings) the
   pure module classifies serve, overhead, volley, forehand or backhand, or
   leaves it unknown.

Doubles: one pose per crop, so a crop holding two players picks one; the
wrist-at-the-ball test then rejects a mismatch rather than naming the wrong
hitter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import av
import numpy as np

from app.analysis.ball_events import (
    FLIGHT_START_FLAG,
    POSE_HIT_WRIST_GATE_M,
    BallEvent,
    PoseHit,
    hitter_ground_point,
)
from app.analysis.court_calibration import CourtCalibration
from app.analysis.homography import image_scale_px_per_m
from app.analysis.player_track import (
    Contact,
    Handedness,
    PlayerFrame,
    classify_stroke,
    contact_candidate,
    estimate_handedness,
    feet_point,
    pose_hit_from_contact,
)
from app.models.court import BallEventKind, CourtEnd
from app.models.enums import ErrorCode
from app.pose.crop import CropBox, crop_pixels, uncrop_landmarks
from app.services.job_cancel import JobCancelled
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
POSE_FPS: Final[float] = 15.0
WINDOW_BEFORE_S: Final[float] = 0.35
WINDOW_AFTER_S: Final[float] = 0.20
#: At most this many ball hits are looked at; a longer match is sampled.
MAX_TRACKED_HITS: Final[int] = 160
#: The crop around the ball, in metres at the ball's depth: sideways each way,
#: up (a raised racket and a head) and down (the feet, a serve's long drop).
CROP_SIDE_M: Final[float] = 2.0
CROP_UP_M: Final[float] = 1.6
CROP_DOWN_M: Final[float] = 3.0
#: A crop smaller than this many pixels each way is grown to it: a tighter
#: crop upscales noise, and a distant ball's scale estimate is rough.
MIN_CROP_PX: Final[float] = 160.0


@dataclass(frozen=True)
class PlayerTrackingReport:
    """What the pass did, for the log and the tests."""

    hits_considered: int
    hits_matched: int
    frames_posed: int
    strokes_classified: int
    handedness: dict[str, str | None] = field(default_factory=dict)
    error: str | None = None


@dataclass
class _Window:
    event: BallEvent
    start_s: float
    end_s: float
    box: CropBox
    frames: list[PlayerFrame] = field(default_factory=list)
    extractor: PoseExtractor | None = None


def hit_crop(calibration: CourtCalibration, ball_xy_px: tuple[float, float]) -> CropBox:
    """The box, as fractions of the frame, that holds the hitter at a hit: the
    ball's picture position, and a box in metres around it at that scale."""
    width, height = calibration.frame_size
    scale = image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, ball_xy_px)
    u, v = ball_xy_px
    left = max(CROP_SIDE_M * scale, MIN_CROP_PX / 2.0)
    up = max(CROP_UP_M * scale, MIN_CROP_PX / 2.0)
    down = max(CROP_DOWN_M * scale, MIN_CROP_PX / 2.0)
    return (
        min(max((u - left) / width, 0.0), 1.0),
        min(max((v - up) / height, 0.0), 1.0),
        min(max((u + left) / width, 0.0), 1.0),
        min(max((v + down) / height, 0.0), 1.0),
    )


def hit_events(events: Sequence[BallEvent]) -> list[BallEvent]:
    """The ball hits worth a pose look, sampled evenly past the cap."""
    hits = [e for e in events if e.kind is BallEventKind.HIT]
    if len(hits) <= MAX_TRACKED_HITS:
        return hits
    step = len(hits) / MAX_TRACKED_HITS
    return [hits[int(i * step)] for i in range(MAX_TRACKED_HITS)]


def _collect_frames(
    video: Path,
    windows: list[_Window],
    progress: Callable[[float], None] | None,
) -> None:
    """One sequential decode; pose only inside each window, on its crop."""
    if not windows:
        return
    model = resolve_model_path()
    windows.sort(key=lambda w: w.start_s)
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
        first = windows[0].start_s
        if first > 0.0:
            try:
                container.seek(int(first / time_base), stream=stream, backward=True, any_frame=False)
            except av.FFmpegError:
                container.seek(0)
        rotation: int | None = None
        last_end = max(w.end_s for w in windows)
        next_window = 0
        active: list[_Window] = []
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            if rotation is None:
                rotation = _display_rotation(stream, container)
            time_s = float(frame.pts) * time_base
            if time_s > last_end:
                break
            while next_window < len(windows) and windows[next_window].start_s <= time_s:
                active.append(windows[next_window])
                next_window += 1
            for done in [w for w in active if w.end_s < time_s]:
                _close(done)
                active.remove(done)
            if not active or time_s - last_posed < spacing * 0.9:
                continue
            last_posed = time_s
            rgb = apply_rotation(frame.to_ndarray(format="rgb24"), rotation)
            for window in active:
                crop = resize_to_long_edge(crop_pixels(rgb, window.box), POSE_LONG_EDGE_PX)
                if window.extractor is None:
                    window.extractor = PoseExtractor(model, num_poses=1)
                landmarks, _, detected = window.extractor.detect_frame(crop, int(round(time_s * 1000.0)))
                if detected:
                    window.frames.append(
                        PlayerFrame(time_s=time_s, landmarks=uncrop_landmarks(landmarks, window.box))
                    )
            if progress is not None:
                progress(min(1.0, (time_s - first) / max(last_end - first, 1e-6)))
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()
        for window in windows:
            _close(window)


def _close(window: _Window) -> None:
    if window.extractor is not None:
        window.extractor.close()
        window.extractor = None


def _end_of_hitter(
    calibration: CourtCalibration, contact: Contact, event: BallEvent
) -> tuple[CourtEnd | None, tuple[float, float] | None]:
    """The hitter's end and ground position: the half their feet are on; else
    the ball event's own end (and no position)."""
    feet = feet_point(contact.landmarks, contact.frame_size)
    ground = None if feet is None else hitter_ground_point(calibration, feet)
    if ground is not None:
        return (CourtEnd.FAR if ground[1] >= 0.0 else CourtEnd.NEAR), ground
    if event.end in (CourtEnd.NEAR, CourtEnd.FAR):
        return event.end, None
    return None, None


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
    windows = [
        _Window(
            event=h,
            start_s=max(0.0, h.time_s - WINDOW_BEFORE_S),
            end_s=h.time_s + WINDOW_AFTER_S,
            box=hit_crop(calibration, h.image_xy_px),
        )
        for h in hits
    ]
    try:
        _collect_frames(video, windows, progress)
    except JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - tracking is optional; the ball result stands
        logger.exception("player tracking failed")
        return (), PlayerTrackingReport(len(hits), 0, 0, 0, error=f"{type(exc).__name__}: {exc}")

    posed = sum(len(w.frames) for w in windows)
    contacts: list[tuple[BallEvent, CourtEnd, tuple[float, float] | None, Contact]] = []
    for window in windows:
        event = window.event
        scale = image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, event.image_xy_px)
        contact = contact_candidate(window.frames, event.time_s, event.image_xy_px, size, scale)
        if contact is None:
            continue
        end, ground = _end_of_hitter(calibration, contact, event)
        if end is not None:
            contacts.append((event, end, ground, contact))

    handedness: dict[CourtEnd, Handedness | None] = {
        end: estimate_handedness([c.wrist for _, e, _, c in contacts if e is end])
        for end in (CourtEnd.NEAR, CourtEnd.FAR)
    }
    pose_hits: list[PoseHit] = []
    classified = 0
    for event, end, ground, contact in contacts:
        scale = image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, event.image_xy_px)
        stroke = classify_stroke(
            contact,
            handedness[end],
            launch=FLIGHT_START_FLAG in event.flags,
            ground_along_m=None if ground is None else ground[1],
        )
        classified += stroke is not None
        pose_hits.append(
            pose_hit_from_contact(event.time_s, contact, end, stroke, POSE_HIT_WRIST_GATE_M * scale)
        )
    pose_hits.sort(key=lambda h: h.time_s)
    report = PlayerTrackingReport(
        hits_considered=len(hits),
        hits_matched=len(contacts),
        frames_posed=posed,
        strokes_classified=classified,
        handedness={end.value: handedness[end] for end in handedness},
    )
    return tuple(pose_hits), report


__all__ = [
    "PlayerTrackingReport",
    "hit_crop",
    "hit_events",
    "track_players",
]
