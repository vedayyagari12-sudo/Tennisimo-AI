"""``app/services/player_tracking.py``: crop planning, windows, and the whole
pass over a small real video with a fake pose model.

The video is a few seconds of plain frames written with OpenCV and decoded by
PyAV; the pose model is replaced by one that returns a skeleton whose right
wrist swings through a known point at known times.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.analysis.ball_events import FLIGHT_START_FLAG, BallEvent
from app.models.court import BallEventKind, CourtEnd
from app.services import player_tracking as module
from app.services.player_tracking import (
    MAX_TRACKED_HITS,
    PlayerTrackingReport,
    hit_events,
    plan_crops,
    track_players,
    windows_for,
)
from tests.unit.analysis.test_ball_events import CALIBRATION, CAMERA
from tests.unit.analysis.test_player_track import L_WRIST, R_WRIST, SIZE, skeleton

HIT_TIMES = (1.0, 2.0, 3.0, 4.0)
#: Where the near player stands, on the court (x, y in metres).
FEET_M = (1.0, -12.5)


def event(time_s: float, xy: tuple[float, float], *, end: CourtEnd | None = None, flags: tuple[str, ...] = ()) -> BallEvent:
    return BallEvent(
        event_id=f"h_{int(time_s * 1000):06d}",
        flight_id=1,
        kind=BallEventKind.HIT,
        time_s=time_s,
        time_sigma_s=0.01,
        image_xy_px=xy,
        image_cov_px2=np.zeros((2, 2)),
        velocity_before_px_s=(0.0, 0.0),
        velocity_after_px_s=(0.0, 0.0),
        confidence=0.9,
        flags=flags,
        end=end,
    )


# ------------------------------------------------------------------ pure


def test_each_halfs_crop_covers_its_own_players() -> None:
    crops = plan_crops(CALIBRATION)
    width, height = CALIBRATION.frame_size
    for end, box in crops.items():
        assert 0.0 <= box[0] < box[2] <= 1.0 and 0.0 <= box[1] < box[3] <= 1.0, (end, box)
    # A standing player's head and feet, at the baseline and mid-half, are inside.
    for end, sign in ((CourtEnd.FAR, 1.0), (CourtEnd.NEAR, -1.0)):
        box = crops[end]
        for along in (sign * 11.0, sign * 6.0):
            pixels = CAMERA.project(np.array([[0.0, along, 0.0], [0.0, along, 1.8]]))
            for u, v in pixels:
                assert box[0] * width <= u <= box[2] * width, (end, u)
                assert box[1] * height <= v <= box[3] * height, (end, v)
    # The far half is higher in the picture than the near half.
    assert crops[CourtEnd.FAR][1] < crops[CourtEnd.NEAR][1]


def test_windows_merge_and_stay_in_time_order() -> None:
    windows = windows_for([5.0, 1.0, 1.2, 9.0])
    assert windows == sorted(windows)
    assert len(windows) == 3  # 1.0 and 1.2 overlap
    assert windows[0][0] == pytest.approx(1.0 - module.WINDOW_BEFORE_S)
    assert windows[0][1] == pytest.approx(1.2 + module.WINDOW_AFTER_S)
    assert windows_for([0.1])[0][0] == 0.0
    assert windows_for([]) == []


def test_only_ball_hits_are_looked_at_and_a_long_match_is_sampled() -> None:
    bounce = event(1.0, (0.0, 0.0))
    bounce = BallEvent(**{**bounce.__dict__, "kind": BallEventKind.BOUNCE})
    hits = [event(float(i), (0.0, 0.0)) for i in range(MAX_TRACKED_HITS * 2)]
    picked = hit_events([bounce, *hits])
    assert len(picked) == MAX_TRACKED_HITS
    assert all(e.kind is BallEventKind.HIT for e in picked)
    assert picked == sorted(picked, key=lambda e: e.time_s)
    assert hit_events([bounce]) == []


# ------------------------------------------------------- the whole pass


class SwingingPlayer:
    """Stands in for ``PoseExtractor``: a near player, facing away, whose
    right wrist passes the ball's position at every hit time."""

    def __init__(self, model: Path, **_: object) -> None:
        self.closed = False
        self.calls = 0

    def detect_frame(self, frame_rgb: np.ndarray, timestamp_ms: int) -> tuple[np.ndarray, np.ndarray, bool]:
        self.calls += 1
        t = timestamp_ms / 1000.0
        nearest = min(HIT_TIMES, key=lambda h: abs(h - t))
        feet = CAMERA.project(np.array([[*FEET_M, 0.0]]))[0]
        hip = (float(feet[0]), float(feet[1]) - 160.0)
        wrist_dx = 700.0 * (t - nearest) + 140.0  # passes +140 px right of the hip at the hit
        return skeleton(hip, right_wrist=(wrist_dx, 10.0)), np.zeros((33, 3), np.float32), True

    def close(self) -> None:
        self.closed = True


def write_video(path: Path, seconds: float = 5.0, fps: float = 30.0) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    for _ in range(int(seconds * fps)):
        writer.write(np.full((180, 320, 3), 90, dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture()
def identity_crops(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake returns FULL-frame fractions, so no crop is applied."""
    whole = (0.0, 0.0, 1.0, 1.0)
    monkeypatch.setattr(module, "plan_crops", lambda calibration: {CourtEnd.NEAR: whole, CourtEnd.FAR: whole})
    monkeypatch.setattr(module, "uncrop_landmarks", lambda landmarks, box: landmarks)
    monkeypatch.setattr(module, "PoseExtractor", SwingingPlayer)


def ball_events() -> list[BallEvent]:
    feet = CAMERA.project(np.array([[*FEET_M, 0.0]]))[0]
    hip = (float(feet[0]), float(feet[1]) - 160.0)
    ball = (hip[0] + 140.0, hip[1] + 10.0)
    return [event(t, ball, flags=(FLIGHT_START_FLAG,) if t == HIT_TIMES[0] else ()) for t in HIT_TIMES]


def test_the_hitter_their_feet_and_their_strokes_are_found(tmp_path: Path, identity_crops: None) -> None:
    video = write_video(tmp_path / "match.mp4")
    seen: list[float] = []
    hits, report = track_players(video, CALIBRATION, ball_events(), progress=seen.append)

    assert isinstance(report, PlayerTrackingReport) and report.error is None
    assert report.hits_considered == 4 and report.hits_matched == 4
    assert report.frames_posed > 0
    assert report.handedness["near"] == "right"
    assert len(hits) == 4
    assert [h.time_s for h in hits] == list(HIT_TIMES)
    assert all(h.end is CourtEnd.NEAR for h in hits)
    assert all(h.feet_xy_px is not None for h in hits)
    # The feet map back to where the player stands.
    feet_px = np.array(hits[0].feet_xy_px)
    expected = CAMERA.project(np.array([[*FEET_M, 0.0]]))[0]
    assert np.linalg.norm(feet_px - (expected + np.array([0.0, 0.0]))) < 200.0
    # Right of the body for a right-hander facing away: forehands.
    assert [h.shot_type for h in hits][1:] == ["forehand"] * 3
    assert seen and max(seen) <= 1.0


def test_a_swing_nowhere_near_the_ball_is_not_credited(tmp_path: Path, identity_crops: None) -> None:
    far_ball = [event(t, (50.0, 50.0)) for t in HIT_TIMES]
    hits, report = track_players(write_video(tmp_path / "m.mp4"), CALIBRATION, far_ball)
    assert hits == () and report.hits_matched == 0 and report.hits_considered == 4


def test_pose_is_run_only_around_the_hits(tmp_path: Path, identity_crops: None) -> None:
    hits, report = track_players(write_video(tmp_path / "m.mp4", seconds=5.0), CALIBRATION, ball_events()[:1])
    # One hit at 1.0 s: a 0.7 s window at about 15 poses a second, not 150 frames.
    assert 0 < report.frames_posed <= 15


def test_a_pose_failure_leaves_the_ball_result_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("model missing")

    monkeypatch.setattr(module, "resolve_model_path", broken)
    hits, report = track_players(write_video(tmp_path / "m.mp4"), CALIBRATION, ball_events())
    assert hits == () and report.error is not None and "model missing" in report.error


def test_no_hits_means_no_work(tmp_path: Path) -> None:
    hits, report = track_players(tmp_path / "does-not-exist.mp4", CALIBRATION, [])
    assert hits == () and report == PlayerTrackingReport(0, 0, 0, 0)


def test_left_hander_wrist_constants_are_distinct() -> None:
    assert L_WRIST != R_WRIST and SIZE == (1920, 1080)
