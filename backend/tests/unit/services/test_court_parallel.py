"""``app/services/court_parallel.py``: the parts that decide how a video is cut,
and the exactness of the cut itself on a small real video.

The cut must give the SAME arrays as one core going through the frames in
order, so ``test_a_parallel_pass_is_identical_to_a_one_core_pass`` runs both
over a short clip with moving, bright blobs and compares every field.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.analysis.flight_track import FLIGHT_CANDIDATE_DTYPE
from app.ball.camera_motion import CameraMotionSampler
from app.services import court_parallel as module
from app.services.court_parallel import (
    MIN_RANGE_S,
    WORKERS_ENV,
    number_candidates,
    split_ranges,
    worker_count,
)

# ------------------------------------------------------------------ pure


def test_ranges_cover_the_span_once_with_the_last_open_ended() -> None:
    ranges = split_ranges(2.0, 62.0, 4)
    assert len(ranges) == 4
    assert ranges[0][0] == 2.0
    assert ranges[-1][1] == math.inf
    for (_, end), (start, _) in zip(ranges, ranges[1:], strict=False):
        assert end == start  # no gap, no overlap
    assert all(end - start >= MIN_RANGE_S for start, end in ranges[:-1])


def test_a_short_span_gets_fewer_workers_than_asked() -> None:
    assert len(split_ranges(0.0, 3 * MIN_RANGE_S + 1, 8)) == 3
    assert len(split_ranges(0.0, 0.5 * MIN_RANGE_S, 8)) == 1
    assert split_ranges(5.0, 5.0, 4) == [(5.0, math.inf)]
    assert split_ranges(10.0, 4.0, 4) == [(10.0, math.inf)]  # a span that is not positive


def test_the_worker_count_follows_the_environment_then_the_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKERS_ENV, "3")
    assert worker_count() == 3
    monkeypatch.setenv(WORKERS_ENV, "0")
    assert worker_count() == 1
    monkeypatch.setenv(WORKERS_ENV, "many")
    assert 1 <= worker_count() <= module.DEFAULT_MAX_WORKERS
    monkeypatch.delenv(WORKERS_ENV)
    assert 1 <= worker_count() <= module.DEFAULT_MAX_WORKERS


def test_candidates_are_numbered_by_their_frames_position() -> None:
    times = np.array([0.0, 0.1, 0.2, 0.3, 0.4], dtype=np.float64)
    records = np.zeros(3, dtype=FLIGHT_CANDIDATE_DTYPE)
    records["time_s"] = [0.2, 0.2, 0.4]
    numbered = number_candidates(records, times)
    assert list(numbered["frame_index"]) == [2, 2, 4]
    assert list(records["frame_index"]) == [0, 0, 0]  # the input is not changed


def test_the_sampler_says_which_frames_it_will_measure() -> None:
    sampler = CameraMotionSampler(sample_interval_s=0.5)
    frame = np.full((90, 160, 3), 100, dtype=np.uint8)
    assert sampler.wants(0.0)  # the reference
    sampler.add(0.0, frame)
    assert not sampler.wants(0.2)
    assert not sampler.wants(0.4999)
    assert sampler.wants(0.5)
    sampler.add(0.5, frame)
    assert not sampler.wants(0.7)
    assert sampler.wants(1.0)
    # What it says it wants is what it uses.
    before = len(sampler.result().times_s)
    sampler.add(0.7, frame)
    assert len(sampler.result().times_s) == before
    sampler.add(1.0, frame)
    assert len(sampler.result().times_s) == before + 1


# ------------------------------------------------- the cut is exact (real decode)


def write_clip(path: Path, seconds: float = 3.0, fps: float = 30.0) -> Path:
    """A grey frame with a few bright balls crossing it, so the detector has
    something to find in every part of the video."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    total = int(seconds * fps)
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 12, (180, 320, 3), dtype=np.uint8)
    for i in range(total):
        frame = np.full((180, 320, 3), 90, dtype=np.uint8) + noise
        for k in range(3):
            x = int((i * (5 + 2 * k) + 40 * k) % 300) + 10
            y = 40 + 40 * k + int(10 * math.sin(i / 6.0 + k))
            cv2.circle(frame, (x, y), 4, (60, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return path


def test_a_parallel_pass_is_identical_to_a_one_core_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.analysis.court_calibration import CourtCalibration
    from app.ball.flight_frames import probe_court_clip
    from app.services import court_offline as offline
    from tests.unit.analysis.test_ball_events import CALIBRATION

    video = write_clip(tmp_path / "clip.mp4", seconds=3.0)
    probe = probe_court_clip(video)

    # A calibration for the 1920x1080 test camera does not fit this 320x180
    # picture, so give both passes the same fixed calibration at the clip's own
    # size: scale the camera's homography into the small frame.
    scale = 320.0 / CALIBRATION.frame_size[0]
    s_matrix = np.diag([scale, scale, 1.0])
    small = CourtCalibration(
        h_court_to_image=s_matrix @ CALIBRATION.h_court_to_image,
        h_image_to_court=CALIBRATION.h_image_to_court @ np.linalg.inv(s_matrix),
        pose=CALIBRATION.pose,
        frame_size=(320, 180),
        taps=CALIBRATION.taps,
        reprojection_rms_px=CALIBRATION.reprojection_rms_px,
        reprojection_max_px=CALIBRATION.reprojection_max_px,
        sigma_tap_px=CALIBRATION.sigma_tap_px,
        calls_allowed=CALIBRATION.calls_allowed,
        flags=CALIBRATION.flags,
        version=CALIBRATION.version,
    )

    class Verdict:
        calibration = small
        rejection = None

    monkeypatch.setattr(offline, "auto_calibration_fit", lambda frame: offline.AutoFit(Verdict(), 0.95, 10))
    # Small clip: start workers on whatever remains after the first frames.
    monkeypatch.setattr(offline, "PHASE1_LAST_INDEX", 20)
    monkeypatch.setattr(offline, "AUTO_FIT_FRAMES", (0,))
    monkeypatch.setattr(module, "MIN_RANGE_S", 0.4)
    monkeypatch.setattr(offline, "MIN_RANGE_S", 0.4)

    one = offline._extract_sequential(video, None, None, probe)
    many = offline._extract_parallel(video, None, None, probe, 3)

    assert np.array_equal(one.frame_times_s, many.frame_times_s)
    assert len(one.candidates) == len(many.candidates) > 0
    for name in one.candidates.dtype.names or ():
        assert np.array_equal(one.candidates[name], many.candidates[name]), name
    assert np.array_equal(one.per_frame_counts, many.per_frame_counts)
    assert one.track_size == many.track_size and one.calibration_source == many.calibration_source
    assert one.stability == many.stability
    assert one.median_feature_count == many.median_feature_count


def test_a_video_with_no_court_is_not_searched_for_balls_past_the_fit_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """No court on any fit frame means the session is refused as not
    calibrated and no candidate is read, so none is searched for."""
    from app.services import court_offline as offline

    monkeypatch.setattr(offline, "auto_calibration_fit", lambda frame: None)
    state = offline._BallPass(None, skip_without_court=True)
    frame = np.full((72, 128, 3), 90, dtype=np.uint8)
    last_fit = max(offline.AUTO_FIT_FRAMES)
    for index in range(last_fit + 25):
        state.step(index, index / 30.0, frame)
    # The three-frame window first fills at frame 2, so there are chunks for
    # frames 2 .. last_fit, and none after.
    assert len(state.chunks) == last_fit - 1
    assert state.source is None


def test_a_slow_failed_court_fit_is_not_repeated_on_later_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import court_offline as offline

    calls: list[int] = []

    def failing_fit(frame: object) -> None:
        calls.append(1)

    monkeypatch.setattr(offline, "auto_calibration_fit", failing_fit)
    monkeypatch.setattr(offline, "AUTO_FIT_BUDGET_S", -1.0)  # the first try already used it all
    state = offline._BallPass(None, skip_without_court=True)
    frame = np.full((72, 128, 3), 90, dtype=np.uint8)
    for index in range(max(offline.AUTO_FIT_FRAMES) + 5):
        state.step(index, index / 30.0, frame)
    assert len(calls) == 1  # frame 0 only; frames 15 and 45 were not tried
    assert len(state.chunks) <= 1  # and no ball search past it


def test_a_fast_failed_court_fit_is_retried_on_each_fit_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import court_offline as offline

    calls: list[int] = []
    monkeypatch.setattr(offline, "auto_calibration_fit", lambda frame: calls.append(1))
    state = offline._BallPass(None, skip_without_court=True)
    frame = np.full((72, 128, 3), 90, dtype=np.uint8)
    for index in range(max(offline.AUTO_FIT_FRAMES) + 5):
        state.step(index, index / 30.0, frame)
    assert len(calls) == len(offline.AUTO_FIT_FRAMES)


def test_without_the_skip_ball_candidates_are_searched_even_with_no_court(monkeypatch: pytest.MonkeyPatch) -> None:
    """The evaluation tools measure candidates without a court, so the shortcut
    is the production job's choice, not the default."""
    from app.services import court_offline as offline

    monkeypatch.setattr(offline, "auto_calibration_fit", lambda frame: None)
    state = offline._BallPass(None)
    frame = np.full((72, 128, 3), 90, dtype=np.uint8)
    last_fit = max(offline.AUTO_FIT_FRAMES)
    for index in range(last_fit + 25):
        state.step(index, index / 30.0, frame)
    assert len(state.chunks) == last_fit + 25 - 2
