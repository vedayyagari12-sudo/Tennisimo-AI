"""The court path's own probe: up to 10 minutes, single-swing limits untouched."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ball.flight_frames import COURT_MAX_CLIP_S, probe_court_clip
from app.models.enums import ErrorCode
from app.pose import video_io
from app.pose.video_io import VideoDecodeError, probe_clip
from tests.unit.seam_clips import write_clip


def test_single_swing_limits_are_unchanged() -> None:
    assert video_io.MAX_CLIP_S == 60.0
    assert video_io.MIN_CLIP_S == 2.0
    assert COURT_MAX_CLIP_S == 600.0


def test_a_61_second_clip_is_court_only(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "long.mp4", frame_count=122, width=64, height=48, fps=2)
    with pytest.raises(VideoDecodeError) as refused:
        probe_clip(clip)
    assert refused.value.error_code is ErrorCode.VIDEO_TOO_LONG
    probe = probe_court_clip(clip)
    assert probe.duration_s == pytest.approx(61.0, abs=0.6)
    assert (probe.width_px, probe.height_px, probe.rotation_deg) == (64, 48, 0)
    assert probe.source_fps == pytest.approx(2.0)


def test_over_ten_minutes_is_refused(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "too_long.mp4", frame_count=602, width=64, height=48, fps=1)
    with pytest.raises(VideoDecodeError) as refused:
        probe_court_clip(clip)
    assert refused.value.error_code is ErrorCode.VIDEO_TOO_LONG


def test_too_short_and_unreadable(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "short.mp4", frame_count=25, width=64, height=48, fps=25)
    with pytest.raises(VideoDecodeError) as refused:
        probe_court_clip(clip)
    assert refused.value.error_code is ErrorCode.VIDEO_TOO_SHORT
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(VideoDecodeError) as broken:
        probe_court_clip(junk)
    assert broken.value.error_code is ErrorCode.DECODE_FAILED


def test_matches_probe_clip_inside_the_single_swing_limit(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "normal.mp4", frame_count=100, width=160, height=96, fps=25)
    assert probe_court_clip(clip) == probe_clip(clip)
