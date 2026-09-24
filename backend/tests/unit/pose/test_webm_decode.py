"""Proof that a browser-recorded WebM actually decodes, end to end.

`ALLOWED_CONTENT_TYPES` accepting `video/webm` is worth nothing on its own: the
API boundary can say yes to a container the pipeline then fails to demux, which
turns a clean 400 at ticket time into a DECODE_FAILED after the student has
already waited out a 50 MB upload. So the acceptance decision is backed by this
file, which encodes real VP8 and VP9 Matroska/WebM clips with PyAV and pushes
them through the SAME entry points the job uses:

* Stage 5  -- `app.pose.video_io.probe_clip` / `open_pose_stream`
* Stage 10 -- `app.ball.frames.load_measurement_window_bgr`

Both are asserted against an H.264/MP4 clip built from the identical synthetic
frames, because "webm works" only means anything relative to the format that
already worked. The assertions are on the timestamps and the pixels, not on the
absence of an exception -- a decoder that returns no frames also does not raise.

SCOPE OF THE CLAIM: these tests prove the decode on whatever PyAV build is
installed where they run. The deployed image installs `av` from the
manylinux wheel, whose bundled FFmpeg is separately configured `--enable-libvpx`
and ships `libvpx.so`; `test_the_local_pyav_build_has_the_webm_decoders` below
asserts the codec/format availability that the container must also satisfy, so
a build without VP8/VP9 fails loudly here rather than silently in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import av
import numpy as np
import numpy.typing as npt
import pytest

from app.ball.frames import load_measurement_window_bgr
from app.pose.video_io import open_pose_stream, probe_clip
from tests.unit.seam_clips import BALL_RGB, ball_centre_at, dominant_ball_pixel, paint_frame

#: Small and short on purpose: libvpx is slower than x264, and nothing under
#: test cares about resolution. 4.0 s clears `video_io.MIN_CLIP_S` (2.0 s) and
#: stays well under `MOTION_SCAN_THRESHOLD_S` so the window is the whole clip.
WIDTH: Final[int] = 160
HEIGHT: Final[int] = 96
FPS: Final[int] = 25
FRAME_COUNT: Final[int] = 100
DURATION_S: Final[float] = FRAME_COUNT / FPS

#: (encoder, expected decoder name reported by the demuxer).
WEBM_CODECS: Final[tuple[tuple[str, str], ...]] = (("libvpx", "vp8"), ("libvpx-vp9", "vp9"))


def write_webm_clip(path: Path, *, encoder: str) -> Path:
    """Encode the shared synthetic frames as Matroska/WebM and return the path.

    `format="webm"` is passed explicitly rather than inferred from the suffix,
    so a test that writes to a differently named file still gets the container
    a browser would have produced.
    """
    container = av.open(str(path), mode="w", format="webm")
    try:
        stream = container.add_stream(encoder, rate=FPS)
        stream.width = WIDTH
        stream.height = HEIGHT
        stream.pix_fmt = "yuv420p"
        # Near-lossless and fast: the colour assertion needs the hue to survive,
        # and libvpx at its default deadline is slow enough to notice.
        stream.options = {"crf": "4", "b": "0", "deadline": "realtime", "cpu-used": "8"}
        for index in range(FRAME_COUNT):
            rgb = paint_frame(WIDTH, HEIGHT, ball_centre_at(index, FRAME_COUNT, WIDTH, HEIGHT))
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def write_mp4_clip(path: Path) -> Path:
    """The H.264/MP4 control, from the identical frames."""
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=FPS)
        stream.width = WIDTH
        stream.height = HEIGHT
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "0", "preset": "ultrafast", "tune": "zerolatency"}
        for index in range(FRAME_COUNT):
            rgb = paint_frame(WIDTH, HEIGHT, ball_centre_at(index, FRAME_COUNT, WIDTH, HEIGHT))
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


@pytest.fixture(scope="module")
def webm_clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("webm")
    return {
        expected: write_webm_clip(directory / f"{expected}.webm", encoder=encoder)
        for encoder, expected in WEBM_CODECS
    }


@pytest.fixture(scope="module")
def mp4_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_mp4_clip(tmp_path_factory.mktemp("webm_control") / "control.mp4")


def _pose_timestamps(path: Path) -> list[float]:
    stream = open_pose_stream(path, long_edge_px=640)
    return [timestamp for timestamp, _ in stream.frames]


# --------------------------------------------------------------------------- #
# The build itself
# --------------------------------------------------------------------------- #


def test_the_local_pyav_build_has_the_webm_decoders() -> None:
    """VP8, VP9 and the matroska/webm demuxer must all be present.

    Asserted rather than assumed: PyAV's FFmpeg is a vendored build, and a
    variant compiled without `--enable-libvpx` would accept `video/webm` at the
    API and then fail every job.
    """
    assert av.codec.Codec("vp8", "r").name == "vp8"
    assert av.codec.Codec("vp9", "r").name == "vp9"
    assert "matroska" in av.format.formats_available or "webm" in av.format.formats_available


# --------------------------------------------------------------------------- #
# Stage 5 -- pose decode
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("codec", [expected for _, expected in WEBM_CODECS])
def test_probe_reports_the_webm_codec_and_the_real_duration(
    webm_clips: dict[str, Path], codec: str
) -> None:
    probe = probe_clip(webm_clips[codec])
    assert probe.codec_name == codec
    assert probe.duration_s == pytest.approx(DURATION_S, abs=0.05)
    assert probe.source_fps == pytest.approx(float(FPS), abs=0.01)
    assert (probe.width_px, probe.height_px) == (WIDTH, HEIGHT)
    assert probe.rotation_deg == 0


@pytest.mark.parametrize("codec", [expected for _, expected in WEBM_CODECS])
def test_frames_come_out_with_absolute_non_decreasing_timestamps(
    webm_clips: dict[str, Path], codec: str
) -> None:
    timestamps = _pose_timestamps(webm_clips[codec])
    assert timestamps, "the decoder yielded nothing"
    assert timestamps[0] == pytest.approx(0.0, abs=1e-6)
    # Within three source-frame periods of the end of the clip. Not exactly the
    # last frame: the final 30 Hz target lands past the last 25 Hz PTS and is
    # dropped rather than duplicated, which the H.264 control does identically
    # (see test_webm_yields_the_same_timeline_as_the_h264_control).
    assert timestamps[-1] == pytest.approx(DURATION_S, abs=3.0 / FPS)
    assert all(later >= earlier for earlier, later in zip(timestamps, timestamps[1:]))
    # Every timestamp is a real PTS on the source grid, not a synthesised k/30.
    for timestamp in timestamps:
        assert timestamp * FPS == pytest.approx(round(timestamp * FPS), abs=0.02)


@pytest.mark.parametrize("codec", [expected for _, expected in WEBM_CODECS])
def test_webm_yields_the_same_timeline_as_the_h264_control(
    webm_clips: dict[str, Path], mp4_clip: Path, codec: str
) -> None:
    webm_timestamps = _pose_timestamps(webm_clips[codec])
    mp4_timestamps = _pose_timestamps(mp4_clip)
    assert len(webm_timestamps) == len(mp4_timestamps)
    for from_webm, from_mp4 in zip(webm_timestamps, mp4_timestamps):
        assert from_webm == pytest.approx(from_mp4, abs=1e-6)


@pytest.mark.parametrize("codec", [expected for _, expected in WEBM_CODECS])
def test_decoded_webm_pixels_are_rgb_and_carry_the_painted_hue(
    webm_clips: dict[str, Path], codec: str
) -> None:
    """Not just "frames arrived": the frames hold the picture that was encoded.

    The disc is optic yellow (high R, high G, low B). A demux that silently
    produced garbage, or a channel order flip, moves this pixel far away.
    """
    stream = open_pose_stream(webm_clips[codec], long_edge_px=640)
    frames = [frame for _, frame in stream.frames]
    middle: npt.NDArray[np.uint8] = frames[len(frames) // 2]
    assert middle.shape == (HEIGHT, WIDTH, 3)
    assert middle.dtype == np.uint8
    pixel = dominant_ball_pixel(middle).astype(int)
    for channel, expected in enumerate(BALL_RGB):
        assert abs(pixel[channel] - expected) <= 24, f"channel {channel}: {pixel.tolist()}"


@pytest.mark.parametrize("codec", [expected for _, expected in WEBM_CODECS])
def test_frame_counts_are_reported_for_webm(webm_clips: dict[str, Path], codec: str) -> None:
    stream = open_pose_stream(webm_clips[codec], long_edge_px=640)
    consumed = list(stream.frames)
    meta = stream.video_meta()
    assert meta.frames_decoded == FRAME_COUNT
    assert meta.frames_sampled == len(consumed)
    assert meta.duration_s == pytest.approx(DURATION_S, abs=0.05)


def test_the_demuxer_goes_by_content_not_by_file_extension(
    webm_clips: dict[str, Path], tmp_path: Path
) -> None:
    """Stage 4 writes the download to a fixed `clip.mp4` temp name.

    WebM bytes therefore always arrive at Stage 5 under an `.mp4` suffix, and
    the whole feature rests on libavformat probing the content rather than
    trusting the name.
    """
    misnamed = tmp_path / "clip.mp4"
    misnamed.write_bytes(webm_clips["vp9"].read_bytes())
    assert probe_clip(misnamed).codec_name == "vp9"
    assert len(_pose_timestamps(misnamed)) > 0


# --------------------------------------------------------------------------- #
# Stage 10 -- the ball decode pass (separate container open, and it SEEKS)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("codec", [expected for _, expected in WEBM_CODECS])
def test_the_ball_measurement_window_seeks_correctly_in_webm(
    webm_clips: dict[str, Path], mp4_clip: Path, codec: str
) -> None:
    """Matroska seeks off cue points, not off an MP4 sample table.

    Different code inside libavformat, so this pass is checked independently of
    Stage 5 -- and against the MP4 control, since an off-by-a-keyframe seek
    would still return plausible-looking frames.
    """
    contact_s = 2.5
    from_webm = load_measurement_window_bgr(
        webm_clips[codec],
        contact_absolute_time_s=contact_s,
        cal_width_px=WIDTH,
        cal_height_px=HEIGHT,
        rotation_deg=0,
    )
    from_mp4 = load_measurement_window_bgr(
        mp4_clip,
        contact_absolute_time_s=contact_s,
        cal_width_px=WIDTH,
        cal_height_px=HEIGHT,
        rotation_deg=0,
    )
    assert len(from_webm.timestamps_s) > 0
    assert len(from_webm.timestamps_s) == len(from_mp4.timestamps_s)
    assert from_webm.timestamps_s == pytest.approx(from_mp4.timestamps_s, abs=1e-6)
    assert all(timestamp >= contact_s for timestamp in from_webm.timestamps_s)
    assert from_webm.background_bgr is not None
    assert from_webm.background_bgr.shape[1:] == (HEIGHT, WIDTH, 3)
