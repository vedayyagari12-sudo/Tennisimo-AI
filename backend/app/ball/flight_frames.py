"""Court mode's decode pass: a stream of TRACK_SPACE BGR frames (design 4.2).

IMPURE: PyAV opens and decodes the file and cv2 resizes. Every decision lives
in the pure-CV ``flight_detector`` / ``camera_motion`` and the pure
``analysis/flight_track``; this module only acquires frames.

* **Streaming, one frame at a time.** A generator, never a list: a 10 minute
  1080p60 session is 36,000 frames, about 223 GB if stacked (design 4.1, the
  reason ``frames.py`` cannot be reused).
* **Real PTS, no resampling.** Every decoded frame is yielded with its own
  absolute PTS in the source file (``frame.pts * time_base``), the same
  convention as ``app/pose/video_io.py``. No frame is dropped or duplicated to
  fit a grid, so variable frame rate stays visible to the linker.
* **BGR.** PyAV is asked for ``bgr24`` directly, as ``app/ball/frames.py`` does;
  the detector's colour gate is HSV from BGR.
* **TRACK_SPACE.** Display-matrix rotation applied, then downscaled (never
  upscaled) to ``TRACK_SPACE_LONG_EDGE_PX``. ``CAL_SPACE`` (1280) is the
  single-swing space and is untouched.
* **Its own probe.** ``probe_clip`` refuses clips over 60 s, the single-swing
  limit; a court session runs up to 10 minutes. ``probe_court_clip`` reads the
  same facts with the same helpers and its own ``COURT_MAX_CLIP_S``, so
  neither ``video_io`` nor ``frames.py`` changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import av
import numpy as np
import numpy.typing as npt

from app.models.enums import ErrorCode
from app.pose.video_io import (
    MIN_CLIP_S,
    ClipProbe,
    VideoDecodeError,
    _display_rotation,
    _packet_scan_duration_s,
    _stream_duration_s,
    apply_rotation,
    resize_to_long_edge,
)

__all__ = [
    "COURT_MAX_CLIP_S",
    "TRACK_SPACE_LONG_EDGE_PX",
    "iter_track_frames",
    "probe_court_clip",
]

#: Design 2.3 / brief Constants: rotated native frames capped at this long edge.
TRACK_SPACE_LONG_EDGE_PX: Final[int] = 1920
#: Court sessions run up to 10 minutes (design 4.6: memory fits a 4 GiB Job).
COURT_MAX_CLIP_S: Final[float] = 600.0


def probe_court_clip(path: Path) -> ClipProbe:
    """``probe_clip`` for the court path: the same duration, rate, rotation and
    size, refused over ``COURT_MAX_CLIP_S`` instead of the 60 s single-swing
    limit. The minimum stays ``MIN_CLIP_S``.

    Raises:
        VideoDecodeError: ``NO_VIDEO_STREAM``, ``UNSUPPORTED_CODEC``,
            ``DECODE_FAILED``, ``VIDEO_TOO_SHORT`` or ``VIDEO_TOO_LONG``.
    """
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc
    try:
        if not container.streams.video:
            raise VideoDecodeError(ErrorCode.NO_VIDEO_STREAM, "no video stream")
        stream = container.streams.video[0]
        codec = stream.codec_context
        if codec is None or codec.name is None:
            raise VideoDecodeError(ErrorCode.UNSUPPORTED_CODEC, "no decodable codec")
        duration_s = _stream_duration_s(stream, container)
        if duration_s <= 0.0:
            duration_s = _packet_scan_duration_s(path, stream.index)
        if duration_s <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "clip has no measurable duration")
        if duration_s < MIN_CLIP_S:
            raise VideoDecodeError(ErrorCode.VIDEO_TOO_SHORT, f"{duration_s:.2f}s is under {MIN_CLIP_S}s")
        if duration_s > COURT_MAX_CLIP_S:
            raise VideoDecodeError(
                ErrorCode.VIDEO_TOO_LONG, f"{duration_s:.2f}s is over {COURT_MAX_CLIP_S}s"
            )
        rate = stream.guessed_rate or stream.average_rate or stream.base_rate
        source_fps = float(rate) if rate else 0.0
        if source_fps <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "clip has no measurable frame rate")
        rotation = _display_rotation(stream, container)
        width, height = int(codec.width), int(codec.height)
        if width <= 0 or height <= 0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "clip has no frame dimensions")
        if rotation in (90, 270):
            width, height = height, width
        return ClipProbe(
            duration_s=duration_s,
            source_fps=source_fps,
            rotation_deg=rotation,
            width_px=width,
            height_px=height,
            codec_name=str(codec.name),
        )
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()


def iter_track_frames(
    path: Path, probe: ClipProbe, *, long_edge_px: int = TRACK_SPACE_LONG_EDGE_PX
) -> Iterator[tuple[float, npt.NDArray[np.uint8]]]:
    """Yield ``(absolute_pts_s, frame_bgr)`` for every decoded frame, in order.

    ``probe`` supplies the display rotation (``probe_clip``). Frames without a
    PTS carry no time and are skipped rather than given an invented one.

    Raises:
        VideoDecodeError: ``DECODE_FAILED`` when the container cannot be opened
            or decoded, or the stream has no time base.
    """
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc
    try:
        stream = container.streams.video[0]
        # Frame-threaded decode, as video_io.py and frames.py do. Bit-identical
        # output; it only lets the decoder use more than one core.
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "stream has no time base")
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            absolute_pts_s = float(frame.pts) * time_base
            bgr = frame.to_ndarray(format="bgr24")
            yield absolute_pts_s, resize_to_long_edge(
                apply_rotation(bgr, probe.rotation_deg), long_edge_px
            )
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()
