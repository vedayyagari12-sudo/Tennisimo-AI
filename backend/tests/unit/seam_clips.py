"""Synthetic video fixtures for the two I/O seams.

A COLOURED fixture, deliberately. A colour-order bug between the pose stream
(RGB, for MediaPipe) and the ball stream (BGR, for cv2) is completely invisible
on a greyscale clip: every channel holds the same value, so reversing them
changes nothing. These clips paint a strongly-hued optic-yellow disc on a dark
background precisely so a swap shows up.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Final

import av
import numpy as np
import numpy.typing as npt

#: ``(frame_index, frame_count, width, height) -> (x, y)``.
CentreFn = Callable[[int, int, int, int], tuple[int, int]]

#: Optic yellow, as RGB. Chosen so the R and B channels are far apart: swapping
#: them moves the HSV hue right out of the ball detector's yellow gate.
BALL_RGB: Final[tuple[int, int, int]] = (210, 235, 40)

#: Dark, desaturated, and far from the yellow gate in both channel orders.
BACKGROUND_RGB: Final[tuple[int, int, int]] = (18, 28, 22)

BALL_RADIUS_PX: Final[int] = 9


def paint_frame(
    width: int, height: int, centre: tuple[int, int]
) -> npt.NDArray[np.uint8]:
    """One RGB frame: a solid disc of :data:`BALL_RGB` on a dark background."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = BACKGROUND_RGB
    ys, xs = np.ogrid[:height, :width]
    disc = (xs - centre[0]) ** 2 + (ys - centre[1]) ** 2 <= BALL_RADIUS_PX**2
    frame[disc] = BALL_RGB
    return frame


def ball_centre_at(frame_index: int, frame_count: int, width: int, height: int) -> tuple[int, int]:
    """Still for the first half of the clip, then a fast diagonal traverse.

    The stillness matters: Stage 5's keyframe motion scan is supposed to place
    the analysis window over the MOVING part, so a clip whose motion is all in
    the second half is what proves the window start is non-zero and that the
    timestamps yielded afterwards are absolute.
    """
    half = frame_count // 2
    if frame_index < half:
        return width // 4, height // 2
    travelled = (frame_index - half) / max(1, frame_count - half - 1)
    x = int(width // 4 + travelled * (width // 2))
    y = int(height // 2 + math.sin(travelled * math.pi) * (height // 6))
    return x, y


def step_centre_at(
    *,
    move_start_s: float,
    move_duration_s: float,
    fps: int,
    width: int,
    height: int,
) -> CentreFn:
    """A ball parked at A, moving to B over ``move_duration_s``, then parked at B.

    A *displaced step*, not a there-and-back burst, and that distinction is the
    whole point: the keyframe pass compares I-frames, so an event that returns
    the scene to its starting state leaves consecutive keyframes identical and
    every bucket energy at zero. A permanent displacement is what makes the
    bucket containing the event win pass 1, while the event's true instant stays
    strictly inside that bucket for pass 2 to find.
    """
    start_index = move_start_s * fps
    move_frames = max(1.0, move_duration_s * fps)
    first = (width // 4, height // 2)
    last = (3 * width // 4, height // 2)

    def centre(frame_index: int, frame_count: int, _w: int, _h: int) -> tuple[int, int]:
        progress = (frame_index - start_index) / move_frames
        progress = min(1.0, max(0.0, progress))
        return (
            int(first[0] + progress * (last[0] - first[0])),
            int(first[1] + progress * (last[1] - first[1])),
        )

    return centre


def write_clip(
    path: Path,
    *,
    frame_count: int = 300,
    width: int = 160,
    height: int = 96,
    fps: int = 25,
    gop_size: int | None = None,
    centre_fn: CentreFn = ball_centre_at,
) -> Path:
    """Encode a synthetic clip and return its path. H.264, yuv420p, no audio.

    ``gop_size`` FORCES the keyframe interval (scene-cut insertion disabled), so
    a test that cares about keyframe spacing states it as an input rather than
    inheriting whatever x264 chose. ``centre_fn`` lets a test place the motion at
    a known instant.
    """
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        # Lossless-ish: the colour assertions need the hue to survive encoding.
        stream.options = {"crf": "0", "preset": "ultrafast", "tune": "zerolatency"}
        if gop_size is not None:
            stream.codec_context.gop_size = gop_size
            # keyint_min + sc_threshold=0: without both, x264 inserts keyframes
            # on scene cuts and the forced spacing is not actually forced.
            stream.options = {
                **stream.options,
                "g": str(gop_size),
                "keyint_min": str(gop_size),
                "sc_threshold": "0",
            }
        for index in range(frame_count):
            rgb = paint_frame(width, height, centre_fn(index, frame_count, width, height))
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def dominant_ball_pixel(frame: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """The brightest-chroma pixel of a frame: the disc, whatever the channel order."""
    data = frame.astype(np.int32)
    spread = data.max(axis=2) - data.min(axis=2)
    index = int(np.argmax(spread))
    row, column = divmod(index, frame.shape[1])
    return frame[row, column]


__all__ = [
    "BACKGROUND_RGB",
    "BALL_RADIUS_PX",
    "BALL_RGB",
    "CentreFn",
    "ball_centre_at",
    "dominant_ball_pixel",
    "paint_frame",
    "step_centre_at",
    "write_clip",
]
