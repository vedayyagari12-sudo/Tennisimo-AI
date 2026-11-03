"""Build step 7: ``app/ball/flight_frames.py`` (design 4.2).

Streaming generator, real PTS (no resampling), BGR, TRACK_SPACE long edge, and
a stream smoke over the vendored clip.
"""

from __future__ import annotations

import inspect
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from app.ball.flight_frames import TRACK_SPACE_LONG_EDGE_PX, iter_track_frames
from app.pose.video_io import ClipProbe, VideoDecodeError, probe_clip
from tests.unit.seam_clips import BALL_RGB, dominant_ball_pixel, paint_frame, write_clip

VENDORED_CLIP = (
    Path(__file__).resolve().parents[2] / "fixtures" / "clips" / "serve_vertical_10340710.mp4"
)
#: Irregular frame times in milliseconds: variable frame rate with gaps.
VFR_PTS_MS: tuple[int, ...] = (0, 33, 50, 100, 117, 200, 217, 233, 300, 301, 400)


def probe_for(width: int, height: int, rotation_deg: int = 0) -> ClipProbe:
    return ClipProbe(
        duration_s=12.0,
        source_fps=25.0,
        rotation_deg=rotation_deg,
        width_px=width,
        height_px=height,
        codec_name="h264",
    )


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("flight_frames") / "clip.mp4")


@pytest.fixture(scope="module")
def vfr_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """H.264 with explicit, irregular PTS in a 1/1000 time base."""
    path = tmp_path_factory.mktemp("flight_frames_vfr") / "vfr.mp4"
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=60)
        stream.width, stream.height, stream.pix_fmt = 160, 96, "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.options = {"crf": "0", "preset": "ultrafast", "tune": "zerolatency"}
        for index, pts in enumerate(VFR_PTS_MS):
            frame = av.VideoFrame.from_ndarray(paint_frame(160, 96, (20 + 10 * index, 48)), format="rgb24")
            frame.pts = pts
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_is_a_lazy_generator(clip: Path) -> None:
    frames = iter_track_frames(clip, probe_for(160, 96))
    assert inspect.isgenerator(frames)
    pts, first = next(frames)
    assert pts == pytest.approx(0.0, abs=1e-9)
    assert first.shape == (96, 160, 3)
    frames.close()


def test_every_frame_once_with_real_increasing_pts(clip: Path) -> None:
    times = [t for t, _ in iter_track_frames(clip, probe_for(160, 96))]
    assert len(times) == 300
    deltas = np.diff(times)
    assert np.all(deltas > 0.0)
    assert np.allclose(deltas, 1.0 / 25.0, atol=1e-6)


def test_variable_frame_rate_pts_are_passed_through_not_resampled(vfr_clip: Path) -> None:
    times = [t for t, _ in iter_track_frames(vfr_clip, probe_for(160, 96))]
    assert len(times) == len(VFR_PTS_MS)
    offset = times[0]  # the muxer may add a constant start offset
    assert np.allclose(np.array(times) - offset, np.array(VFR_PTS_MS) / 1000.0, atol=1e-3)


def test_frames_are_bgr(clip: Path) -> None:
    _, frame = next(iter_track_frames(clip, probe_for(160, 96)))
    pixel = dominant_ball_pixel(frame).astype(int)
    expected_bgr = np.array(BALL_RGB[::-1])
    assert np.all(np.abs(pixel - expected_bgr) <= 6)


def test_long_edge_cap_and_rotation(clip: Path) -> None:
    _, small = next(iter_track_frames(clip, probe_for(160, 96), long_edge_px=80))
    assert small.shape == (48, 80, 3)
    _, rotated = next(iter_track_frames(clip, probe_for(96, 160, rotation_deg=90)))
    assert rotated.shape == (160, 96, 3)
    _, never_upscaled = next(iter_track_frames(clip, probe_for(160, 96), long_edge_px=4000))
    assert never_upscaled.shape == (96, 160, 3)


def test_unreadable_file_raises_decode_error(tmp_path: Path) -> None:
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"this is not a video")
    with pytest.raises(VideoDecodeError):
        list(iter_track_frames(bogus, probe_for(160, 96)))


@pytest.mark.skipif(not VENDORED_CLIP.is_file(), reason=f"vendored clip missing: {VENDORED_CLIP}")
def test_vendored_clip_stream_smoke() -> None:
    probe = probe_clip(VENDORED_CLIP)
    count = 0
    previous = -np.inf
    shape: tuple[int, ...] | None = None
    for pts, frame in iter_track_frames(VENDORED_CLIP, probe):
        assert pts > previous
        previous = pts
        assert frame.dtype == np.uint8 and frame.ndim == 3 and frame.shape[2] == 3
        assert max(frame.shape[:2]) == min(TRACK_SPACE_LONG_EDGE_PX, max(probe.width_px, probe.height_px))
        assert shape is None or frame.shape == shape
        shape = frame.shape
        count += 1
    assert count == pytest.approx(probe.duration_s * probe.source_fps, abs=3)
    assert shape is not None and shape[0] > shape[1]  # portrait after rotation
