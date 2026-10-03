"""A WebM recorded LIVE in a browser has no duration in its headers.

`test_webm_decode.py` proves that a cleanly finished WebM decodes. That is not
what a phone browser sends. Android Chrome's MediaRecorder STREAMS the file, so
the muxer can never go back and patch the Duration (or the Cues index) into the
header. Such a file reports `container.duration is None`, and before the packet
scan fallback `probe_clip` rejected it as `decode_failed: clip has no measurable
duration` -- every live recording from the school web build would have failed.

These tests reproduce that file by writing to a NON-seekable sink (an object
with `write` but no `seek`/`tell`), which is exactly the condition under which
the muxer cannot back-patch.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Final

import av
import pytest

from app.models.enums import ErrorCode
from app.pose.video_io import (
    VideoDecodeError,
    _packet_scan_duration_s,
    open_pose_stream,
    probe_clip,
)
from tests.unit.pose.test_webm_decode import (
    DURATION_S,
    FPS,
    FRAME_COUNT,
    HEIGHT,
    WEBM_CODECS,
    WIDTH,
    write_webm_clip,
)
from tests.unit.seam_clips import ball_centre_at, paint_frame


class NonSeekableSink:
    """`write` only. No `seek`/`tell`, so the muxer cannot back-patch headers."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self.buffer.write(data)

    def flush(self) -> None:
        return None


def write_live_webm(path: Path, *, encoder: str, frame_count: int = FRAME_COUNT) -> Path:
    """Encode the shared synthetic frames the way a streaming recorder writes them."""
    sink = NonSeekableSink()
    container = av.open(sink, mode="w", format="webm")  # type: ignore[call-overload]
    stream = container.add_stream(encoder, rate=FPS)
    stream.width = WIDTH
    stream.height = HEIGHT
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "4", "b": "0", "deadline": "realtime", "cpu-used": "8"}
    for index in range(frame_count):
        rgb = paint_frame(WIDTH, HEIGHT, ball_centre_at(index, frame_count, WIDTH, HEIGHT))
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    path.write_bytes(sink.buffer.getvalue())
    return path


FRAME_INTERVAL_S: Final[float] = 1.0 / FPS


@pytest.mark.parametrize(("encoder", "decoder"), WEBM_CODECS)
def test_the_fixture_really_has_no_duration_in_its_headers(
    tmp_path: Path, encoder: str, decoder: str
) -> None:
    """Guard the guard: if this passes trivially the tests below prove nothing."""
    path = write_live_webm(tmp_path / "live.webm", encoder=encoder)
    container = av.open(str(path))
    try:
        assert container.duration is None
        assert container.streams.video[0].duration is None
    finally:
        container.close()


@pytest.mark.parametrize(("encoder", "decoder"), WEBM_CODECS)
def test_a_live_webm_probes_with_the_duration_a_finished_one_reports(
    tmp_path: Path, encoder: str, decoder: str
) -> None:
    live = probe_clip(write_live_webm(tmp_path / "live.webm", encoder=encoder))
    finished = probe_clip(write_webm_clip(tmp_path / "finished.webm", encoder=encoder))

    assert live.codec_name == decoder
    assert live.duration_s == pytest.approx(DURATION_S, abs=FRAME_INTERVAL_S)
    assert live.duration_s == pytest.approx(finished.duration_s, abs=FRAME_INTERVAL_S)
    assert live.source_fps == pytest.approx(FPS, abs=1.0)


@pytest.mark.parametrize(("encoder", "decoder"), WEBM_CODECS)
def test_a_live_webm_samples_the_same_instants_as_the_same_video_finished(
    tmp_path: Path, encoder: str, decoder: str
) -> None:
    """Same frame count and rate, so the sampled instants must match exactly.

    Frame timestamps depend on the frame index, not the pixels, so the two
    separately encoded files must be sampled at identical instants. A wrong
    duration would shift the analysis window and these lists would diverge.
    """
    live = [pts for pts, _ in open_pose_stream(write_live_webm(tmp_path / "live.webm", encoder=encoder)).frames]
    finished = [
        pts
        for pts, _ in open_pose_stream(write_webm_clip(tmp_path / "finished.webm", encoder=encoder)).frames
    ]

    assert live, "the live WebM produced no frames"
    assert live == sorted(live)
    assert live == pytest.approx(finished, abs=1e-6)


def test_a_file_with_one_packet_is_still_rejected_not_guessed(tmp_path: Path) -> None:
    """A single timestamp cannot give a length. 0.0 must stay 'unmeasurable'."""
    path = write_live_webm(tmp_path / "one_frame.webm", encoder="libvpx", frame_count=1)
    assert _packet_scan_duration_s(path, 0) == 0.0
    with pytest.raises(VideoDecodeError) as caught:
        probe_clip(path)
    assert caught.value.error_code in (ErrorCode.DECODE_FAILED, ErrorCode.VIDEO_TOO_SHORT)


def test_the_packet_scan_never_raises_on_an_unreadable_path(tmp_path: Path) -> None:
    assert _packet_scan_duration_s(tmp_path / "does_not_exist.webm", 0) == 0.0
    garbage = tmp_path / "garbage.webm"
    garbage.write_bytes(b"this is not a video" * 50)
    assert _packet_scan_duration_s(garbage, 0) == 0.0


def test_the_fallback_is_not_used_when_the_headers_already_have_a_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished file must keep taking the cheap header path, untouched."""
    path = write_webm_clip(tmp_path / "finished.webm", encoder="libvpx")

    def must_not_run(*_: object, **__: object) -> float:
        raise AssertionError("packet scan ran although the header had a duration")

    monkeypatch.setattr("app.pose.video_io._packet_scan_duration_s", must_not_run)
    assert probe_clip(path).duration_s == pytest.approx(DURATION_S, abs=FRAME_INTERVAL_S)
