"""``app/services/player_tracking.py``: the crop around each hit, and the whole
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
    hit_crop,
    hit_events,
    track_players,
)
from tests.unit.analysis.test_ball_events import CALIBRATION, CAMERA
from tests.unit.analysis.test_player_track import L_WRIST, R_WRIST, SIZE, skeleton

HIT_TIMES = (1.0, 2.0, 3.0, 4.0)
#: Where the near player stands, on the court (x, y in metres).
NEAR_FEET_M = (1.0, -12.5)
FAR_FEET_M = (-1.0, 12.5)


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


def pixel(x: float, y: float, z: float) -> tuple[float, float]:
    u, v = CAMERA.project(np.array([[x, y, z]]))[0]
    return float(u), float(v)


# ------------------------------------------------------------------ pure


@pytest.mark.parametrize("along", [-12.0, -6.0, 6.0, 12.0])
def test_the_crop_around_a_hit_holds_the_whole_player(along: float) -> None:
    width, height = CALIBRATION.frame_size
    # A groundstroke: the ball is at hip height over the player's own spot.
    ball = pixel(1.0, along, 1.0)
    box = hit_crop(CALIBRATION, ball)
    assert 0.0 <= box[0] < box[2] <= 1.0 and 0.0 <= box[1] < box[3] <= 1.0
    for z in (0.0, 1.9):  # feet and the top of the head
        u, v = pixel(1.0, along, z)
        assert box[0] * width <= u <= box[2] * width, (along, z, u)
        assert box[1] * height <= v <= box[3] * height, (along, z, v)


def test_a_far_player_still_gets_a_usable_crop() -> None:
    far_box = hit_crop(CALIBRATION, pixel(0.0, 11.0, 1.0))
    width, height = CALIBRATION.frame_size
    assert (far_box[2] - far_box[0]) * width >= module.MIN_CROP_PX - 1
    assert (far_box[3] - far_box[1]) * height >= module.MIN_CROP_PX - 1
    near_box = hit_crop(CALIBRATION, pixel(0.0, -11.0, 1.0))
    assert (near_box[2] - near_box[0]) > (far_box[2] - far_box[0])  # nearer is bigger in the picture


def test_a_ball_at_the_frame_edge_gives_a_box_inside_the_frame() -> None:
    box = hit_crop(CALIBRATION, (5.0, 5.0))
    assert all(0.0 <= v <= 1.0 for v in box) and box[0] < box[2] and box[1] < box[3]


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
    """Stands in for ``PoseExtractor``: a player whose right wrist passes the
    ball's position at every hit time. ``feet_m`` and ``facing_away`` are set
    by each test."""

    feet_m: tuple[float, float] = NEAR_FEET_M
    facing_away: bool = True
    detect: bool = True
    created = 0
    closed = 0

    def __init__(self, model: Path, **_: object) -> None:
        type(self).created += 1

    def detect_frame(self, frame_rgb: np.ndarray, timestamp_ms: int) -> tuple[np.ndarray, np.ndarray, bool]:
        if not type(self).detect:
            return np.zeros((33, 4), np.float32), np.zeros((33, 3), np.float32), False
        t = timestamp_ms / 1000.0
        nearest = min(HIT_TIMES, key=lambda h: abs(h - t))
        feet = pixel(*type(self).feet_m, 0.0)
        hip = (feet[0], feet[1] - 160.0)
        sign = 1.0 if type(self).facing_away else -1.0
        wrist_dx = sign * (700.0 * (t - nearest) + 140.0)  # passes 140 px to the player's right at the hit
        return skeleton(hip, facing_away=type(self).facing_away, right_wrist=(wrist_dx, 10.0)), np.zeros((33, 3), np.float32), True

    def close(self) -> None:
        type(self).closed += 1


def write_video(path: Path, seconds: float = 5.0, fps: float = 30.0) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    for _ in range(int(seconds * fps)):
        writer.write(np.full((180, 320, 3), 90, dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture()
def fake_pose(monkeypatch: pytest.MonkeyPatch) -> type[SwingingPlayer]:
    """The fake returns FULL-frame fractions, so the crop is not undone."""
    monkeypatch.setattr(module, "uncrop_landmarks", lambda landmarks, box: landmarks)
    monkeypatch.setattr(module, "PoseExtractor", SwingingPlayer)
    SwingingPlayer.feet_m, SwingingPlayer.facing_away, SwingingPlayer.detect = NEAR_FEET_M, True, True
    SwingingPlayer.created = SwingingPlayer.closed = 0
    return SwingingPlayer


def ball_events(feet_m: tuple[float, float], facing_away: bool, end: CourtEnd | None = None) -> list[BallEvent]:
    feet = pixel(*feet_m, 0.0)
    hip = (feet[0], feet[1] - 160.0)
    sign = 1.0 if facing_away else -1.0
    ball = (hip[0] + sign * 140.0, hip[1] + 10.0)
    return [
        event(t, ball, end=end, flags=(FLIGHT_START_FLAG,) if t == HIT_TIMES[0] else ()) for t in HIT_TIMES
    ]


def test_the_near_hitter_their_feet_and_their_strokes_are_found(tmp_path: Path, fake_pose: type[SwingingPlayer]) -> None:
    seen: list[float] = []
    hits, report = track_players(
        write_video(tmp_path / "match.mp4"), CALIBRATION, ball_events(NEAR_FEET_M, True), progress=seen.append
    )
    assert isinstance(report, PlayerTrackingReport) and report.error is None
    assert report.hits_considered == 4 and report.hits_matched == 4
    assert report.frames_posed > 0
    assert report.handedness["near"] == "right" and report.handedness["far"] is None
    assert [h.time_s for h in hits] == list(HIT_TIMES)
    assert all(h.end is CourtEnd.NEAR for h in hits)  # from where the feet are, not from the event
    assert all(h.feet_xy_px is not None for h in hits)
    assert np.linalg.norm(np.array(hits[0].feet_xy_px) - np.array(pixel(*NEAR_FEET_M, 0.0))) < 200.0
    assert [h.shot_type for h in hits][1:] == ["forehand"] * 3
    assert seen and max(seen) <= 1.0
    # A fresh pose model per hit, and every one released.
    assert fake_pose.created == 4 and fake_pose.closed == 4


def test_a_far_hitter_is_credited_to_the_far_end(tmp_path: Path, fake_pose: type[SwingingPlayer]) -> None:
    fake_pose.feet_m, fake_pose.facing_away = FAR_FEET_M, False
    hits, report = track_players(
        write_video(tmp_path / "m.mp4"), CALIBRATION, ball_events(FAR_FEET_M, False, end=CourtEnd.NEAR)
    )
    assert report.hits_matched == 4
    # The event said near; the feet say far, and the feet win.
    assert all(h.end is CourtEnd.FAR for h in hits)
    assert [h.shot_type for h in hits][1:] == ["forehand"] * 3


def test_a_swing_nowhere_near_the_ball_is_not_credited(tmp_path: Path, fake_pose: type[SwingingPlayer]) -> None:
    far_ball = [event(t, (50.0, 50.0)) for t in HIT_TIMES]
    hits, report = track_players(write_video(tmp_path / "m.mp4"), CALIBRATION, far_ball)
    assert hits == () and report.hits_matched == 0 and report.hits_considered == 4


def test_no_player_detected_means_no_credit_not_a_crash(tmp_path: Path, fake_pose: type[SwingingPlayer]) -> None:
    fake_pose.detect = False
    hits, report = track_players(write_video(tmp_path / "m.mp4"), CALIBRATION, ball_events(NEAR_FEET_M, True))
    assert hits == () and report.hits_matched == 0 and report.frames_posed == 0 and report.error is None


def test_pose_is_run_only_around_the_hits(tmp_path: Path, fake_pose: type[SwingingPlayer]) -> None:
    _, report = track_players(write_video(tmp_path / "m.mp4", seconds=5.0), CALIBRATION, ball_events(NEAR_FEET_M, True)[:1])
    # One hit: a 0.55 s window at 15 poses a second, not 150 frames.
    assert 0 < report.frames_posed <= 9


def test_a_pose_failure_leaves_the_ball_result_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("model missing")

    monkeypatch.setattr(module, "resolve_model_path", broken)
    hits, report = track_players(write_video(tmp_path / "m.mp4"), CALIBRATION, ball_events(NEAR_FEET_M, True))
    assert hits == () and report.error is not None and "model missing" in report.error


def test_no_hits_means_no_work(tmp_path: Path) -> None:
    hits, report = track_players(tmp_path / "does-not-exist.mp4", CALIBRATION, [])
    assert hits == () and report == PlayerTrackingReport(0, 0, 0, 0)


def test_wrist_constants_are_distinct() -> None:
    assert L_WRIST != R_WRIST and SIZE == (1920, 1080)
