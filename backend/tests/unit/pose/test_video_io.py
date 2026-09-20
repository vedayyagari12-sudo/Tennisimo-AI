"""Stage 5 seam: ``app/pose/video_io.py``.

Two things here are contracts the rest of the pipeline silently depends on:

1. **The generator yields ABSOLUTE PTS.** ``extract_keypoints`` subtracts
   ``window_start_s`` itself (``extractor.py:215-228``), so a seam that
   pre-subtracts it subtracts it twice -- invisibly on any clip whose window
   starts at 0, which is every short test clip anyone reaches for first.
2. **The pose stream is RGB.** MediaPipe is handed ``ImageFormat.SRGB``
   (``extractor.py:146``). The ball detector wants BGR. See
   ``tests/unit/ball/test_frames.py`` for the swap test that spans both seams.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.models.enums import ErrorCode
from app.pose.video_io import (
    ANALYSIS_FPS,
    VideoDecodeError,
    analysis_window_bounds,
    apply_rotation,
    open_pose_stream,
    probe_clip,
    resize_to_long_edge,
    rotation_from_display_matrix,
    scaled_size,
    target_times,
)
from tests.unit.seam_clips import BALL_RGB, dominant_ball_pixel, write_clip


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 12 s synthetic clip whose motion is all in the second half."""
    return write_clip(tmp_path_factory.mktemp("video_io") / "clip.mp4")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_scaled_size_preserves_aspect_and_caps_the_long_edge() -> None:
    assert scaled_size(1080, 2048, 640) == (338, 640)
    assert scaled_size(320, 240, 640) == (320, 240), "never upscales"


def test_rotation_from_display_matrix_reads_the_container_rotation() -> None:
    identity = [65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824]
    assert rotation_from_display_matrix(identity) == 0


def test_analysis_window_bounds_clamps_to_the_clip() -> None:
    start, end = analysis_window_bounds(30.0, 2.0, window_s=8.0)
    assert (start, end) == (0.0, 8.0), "a centre near the head clamps to the head"

    start, end = analysis_window_bounds(30.0, 29.0, window_s=8.0)
    assert end <= 30.0
    assert end - start == pytest.approx(8.0)

    start, end = analysis_window_bounds(3.0, None, window_s=8.0)
    assert (start, end) == (0.0, 3.0), "a clip shorter than the window is the window"


def test_target_times_are_spaced_at_the_analysis_rate_and_capped() -> None:
    times = target_times(2.0, 10.0, fps=ANALYSIS_FPS, cap=240)
    assert len(times) == 240
    assert times[0] == pytest.approx(2.0)
    assert times[1] - times[0] == pytest.approx(1.0 / ANALYSIS_FPS)
    assert times == sorted(times)


def test_apply_rotation_and_resize_keep_the_array_contiguous() -> None:
    frame = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    rotated = apply_rotation(frame, 90)
    assert rotated.shape[:2] == (6, 4)
    assert rotated.flags["C_CONTIGUOUS"]

    resized = resize_to_long_edge(frame, 3)
    assert max(resized.shape[:2]) == 3
    assert resized.flags["C_CONTIGUOUS"]


# --------------------------------------------------------------------------- #
# The seam itself
# --------------------------------------------------------------------------- #


def test_probe_reads_real_timing_from_the_container(clip: Path) -> None:
    probe = probe_clip(clip)
    assert probe.duration_s == pytest.approx(12.0, abs=0.2)
    assert probe.source_fps == pytest.approx(25.0, abs=0.5)
    assert (probe.width_px, probe.height_px) == (160, 96)


def test_generator_yields_absolute_pts_not_window_relative(clip: Path) -> None:
    """THE seam contract. A window-relative first timestamp would be ~0.0."""
    stream = open_pose_stream(clip)
    assert stream.motion_scan_used, "motion is in the second half; the scan should fire"
    assert stream.analysis_window_start_s > 0.0

    timestamps = [pts for pts, _ in stream.frames]
    assert timestamps, "the window produced no frames"
    # The sampler picks the frame NEAREST each target, so the first sample may
    # sit a fraction of a frame interval before the window start. That never
    # showed before Defect 1 was fixed only because the window start was then a
    # keyframe PTS verbatim; a refined centre is a midpoint between two frames.
    assert timestamps[0] >= stream.analysis_window_start_s - 1.0 / 25.0
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == pytest.approx(stream.analysis_window_start_s, abs=1.0 / 25.0)
    # The decisive one: NOT relative to the window.
    assert timestamps[0] > 1.0 / ANALYSIS_FPS


def test_video_meta_counts_are_only_valid_after_iteration(clip: Path) -> None:
    stream = open_pose_stream(clip)
    assert stream.video_meta().frames_sampled == 0, "nothing has been decoded yet"

    frames = list(stream.frames)
    meta = stream.video_meta()
    assert meta.frames_sampled == len(frames)
    assert meta.frames_decoded >= meta.frames_sampled
    assert meta.analysis_fps == ANALYSIS_FPS
    # CAL_SPACE never UPSCALES either, so on a 160 px fixture the two spaces
    # coincide; on real footage CAL_SPACE is the larger of the two.
    assert meta.cal_space_width_px >= meta.width_px


def test_frame_cap_truncates_the_window(clip: Path) -> None:
    stream = open_pose_stream(clip, max_frames=20)
    frames = list(stream.frames)
    assert len(frames) == 20


def test_pose_stream_frames_are_rgb(clip: Path) -> None:
    """Channel order asserted from a COLOURED fixture, not from a docstring."""
    stream = open_pose_stream(clip)
    _, frame = next(iter(stream.frames))
    pixel = dominant_ball_pixel(frame)

    assert pixel[0] > pixel[2], "R must exceed B for this fixture; a BGR frame reverses it"
    assert np.allclose(np.asarray(pixel, dtype=np.int32), np.asarray(BALL_RGB), atol=25)


def test_missing_file_raises_a_decode_error_with_a_code(tmp_path: Path) -> None:
    with pytest.raises(VideoDecodeError) as excinfo:
        probe_clip(tmp_path / "absent.mp4")
    assert excinfo.value.error_code in (
        ErrorCode.DECODE_FAILED,
        ErrorCode.NO_VIDEO_STREAM,
        ErrorCode.UNSUPPORTED_CODEC,
    )


def test_non_video_file_raises_rather_than_returning_an_empty_stream(
    tmp_path: Path,
) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video, not even close")
    with pytest.raises(VideoDecodeError):
        probe_clip(junk)
