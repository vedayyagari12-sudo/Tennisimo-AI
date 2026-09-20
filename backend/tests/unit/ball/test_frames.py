"""Stage 10 seam: ``app/ball/frames.py``.

THE TEST THIS MODULE EXISTS FOR is :func:`test_colour_space_swap_is_detected`.
``detect_ball_track`` consumes **BGR** (``detector.py``: ``COLOR_BGR2GRAY``,
``COLOR_BGR2HSV``); MediaPipe consumes **RGB** (``extractor.py:146``,
``ImageFormat.SRGB``). Handing one array to both does not raise: it rotates the
hue channel of the HSV gate that finds the yellow ball, the detector finds
nothing, and the outcome is reported as ``too_few_detections`` -- which looks
exactly like a hard clip. The assertions below fail if the two streams are ever
swapped.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.ball.detector import YELLOW_HSV_LOWER, YELLOW_HSV_UPPER
from app.ball.frames import (
    DETECTION_DEADLINE_S,
    WINDOW_S,
    MeasurementWindow,
    exclusion_boxes_for,
    load_measurement_window_bgr,
    run_ball_detection,
)
from app.analysis.normalize import normalize_sequence
from app.models.enums import BallSpeedUnavailableReason
from app.models.internal import PoseSequence
from app.pose.video_io import open_pose_stream
from tests.unit.seam_clips import BALL_RGB, dominant_ball_pixel, write_clip

CONTACT_S = 7.0


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("ball_frames") / "clip.mp4")


@pytest.fixture(scope="module")
def window(clip: Path) -> MeasurementWindow:
    return load_measurement_window_bgr(
        clip,
        contact_absolute_time_s=CONTACT_S,
        cal_width_px=160,
        cal_height_px=96,
        rotation_deg=0,
    )


def hue_of_ball(frame: np.ndarray) -> int:
    """The HSV hue the DETECTOR would compute for the disc in this frame."""
    hsv = cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_BGR2HSV)
    spread = frame.astype(np.int32).max(axis=2) - frame.astype(np.int32).min(axis=2)
    row, column = np.unravel_index(int(np.argmax(spread)), spread.shape)
    return int(hsv[row, column, 0])


# --------------------------------------------------------------------------- #
# The colour-space trap
# --------------------------------------------------------------------------- #


def test_measurement_window_frames_are_bgr(window: MeasurementWindow) -> None:
    pixel = dominant_ball_pixel(window.frames_bgr[0])
    assert pixel[2] > pixel[0], "B must be the LAST channel; an RGB frame reverses it"
    assert np.allclose(
        np.asarray(pixel, dtype=np.int32),
        np.asarray(BALL_RGB[::-1], dtype=np.int32),
        atol=25,
    )


def test_colour_space_swap_is_detected(clip: Path, window: MeasurementWindow) -> None:
    """The two streams are NOT interchangeable, and this proves it numerically.

    The ball stream's disc lands inside the detector's optic-yellow hue gate.
    The pose stream's identical disc, fed to the same ``COLOR_BGR2HSV`` call,
    lands outside it -- which is precisely the silent failure mode: no
    exception, an empty mask, and a ``too_few_detections`` verdict.
    """
    ball_hue = hue_of_ball(window.frames_bgr[0])

    stream = open_pose_stream(clip)
    _, pose_frame = next(iter(stream.frames))
    pose_hue = hue_of_ball(pose_frame)

    assert YELLOW_HSV_LOWER[0] <= ball_hue <= YELLOW_HSV_UPPER[0], (
        "the BGR ball stream must land inside the detector's yellow gate"
    )
    assert not (YELLOW_HSV_LOWER[0] <= pose_hue <= YELLOW_HSV_UPPER[0]), (
        "the RGB pose stream must NOT land inside it; if this passes, the two "
        "buffers have become interchangeable and the swap is undetectable"
    )


# --------------------------------------------------------------------------- #
# Window acquisition
# --------------------------------------------------------------------------- #


def test_window_is_seeked_off_the_absolute_contact_time(window: MeasurementWindow) -> None:
    assert window.frames_bgr.shape[0] > 0
    assert window.timestamps_s[0] >= CONTACT_S
    assert window.timestamps_s[-1] <= CONTACT_S + WINDOW_S + 1e-6
    assert np.all(np.diff(window.timestamps_s) > 0.0)


def test_native_fps_is_measured_from_pts_not_assumed(window: MeasurementWindow) -> None:
    assert window.native_fps == pytest.approx(25.0, abs=1.0)


def test_background_preroll_is_taken_from_before_contact(
    window: MeasurementWindow,
) -> None:
    assert window.background_bgr is not None
    assert window.background_bgr.shape[1:] == window.frames_bgr.shape[1:]


def test_window_past_the_end_of_the_clip_is_empty_not_fabricated(clip: Path) -> None:
    window = load_measurement_window_bgr(
        clip,
        contact_absolute_time_s=600.0,
        cal_width_px=160,
        cal_height_px=96,
        rotation_deg=0,
    )
    assert window.frames_bgr.shape[0] == 0
    assert window.timestamps_s.size == 0
    assert window.native_fps == 0.0


def test_a_zero_deadline_raises_rather_than_returning_a_short_window(
    clip: Path,
) -> None:
    from app.ball.frames import DetectionDeadlineExceeded

    with pytest.raises(DetectionDeadlineExceeded):
        load_measurement_window_bgr(
            clip,
            contact_absolute_time_s=CONTACT_S,
            cal_width_px=160,
            cal_height_px=96,
            rotation_deg=0,
            deadline_s=-1.0,
        )


# --------------------------------------------------------------------------- #
# Hints and the never-fails contract
# --------------------------------------------------------------------------- #


def synthetic_sequence(frame_count: int = 12) -> PoseSequence:
    """A minimal standing pose: visible, still, and centred in the frame."""
    landmarks = np.zeros((frame_count, 33, 4), dtype=np.float32)
    layout = {
        0: (0.50, 0.20),   # nose
        11: (0.45, 0.30), 12: (0.55, 0.30),   # shoulders
        13: (0.42, 0.40), 14: (0.58, 0.40),   # elbows
        15: (0.40, 0.50), 16: (0.60, 0.50),   # wrists
        23: (0.46, 0.55), 24: (0.54, 0.55),   # hips
        25: (0.46, 0.70), 26: (0.54, 0.70),   # knees
        27: (0.46, 0.85), 28: (0.54, 0.85),   # ankles
    }
    for index, (x, y) in layout.items():
        landmarks[:, index, 0] = x
        landmarks[:, index, 1] = y
        landmarks[:, index, 3] = 0.95
    return PoseSequence(
        landmarks=landmarks,
        world=np.zeros((frame_count, 33, 3), dtype=np.float32),
        timestamps_s=np.arange(frame_count, dtype=np.float64) / 30.0 + CONTACT_S,
        detected=np.ones((frame_count,), dtype=bool),
        width_px=160,
        height_px=96,
    )


def test_exclusion_boxes_are_one_per_measurement_frame() -> None:
    seq, _ = normalize_sequence(synthetic_sequence())
    timestamps = np.asarray([CONTACT_S, CONTACT_S + 0.04, CONTACT_S + 0.08])

    boxes = exclusion_boxes_for(seq, timestamps, cal_scale=2.0)

    assert len(boxes) == len(timestamps)
    for box in boxes:
        assert box is not None
        x0, y0, x1, y1 = box
        assert x1 > x0 and y1 > y0


def test_exclusion_boxes_are_none_when_there_are_no_pose_frames() -> None:
    seq, _ = normalize_sequence(synthetic_sequence())
    empty = type(seq)(
        origin_px=np.zeros((0, 2)),
        points=np.zeros((0, 33, 2)),
        velocity=np.zeros((0, 33, 2)),
        acceleration=np.zeros((0, 33, 2)),
        timestamps_s=np.zeros((0,)),
        valid=np.zeros((0,), dtype=bool),
        visibility=np.zeros((0, 33)),
        swing_direction_sign=1,
        torso_scale_px=seq.torso_scale_px,
        width_px=160,
        height_px=96,
    )

    boxes = exclusion_boxes_for(empty, np.asarray([CONTACT_S]), cal_scale=2.0)

    assert boxes == [None], "no pose frame means no box, never a fabricated one"


def test_run_ball_detection_never_raises_on_a_missing_file(tmp_path: Path) -> None:
    seq, _ = normalize_sequence(synthetic_sequence())

    result, summary, timestamps = run_ball_detection(
        tmp_path / "absent.mp4",
        seq,
        contact_absolute_time_s=CONTACT_S,
        racket_wrist_index=16,
        contact_frame_index=2,
        cal_width_px=160,
        cal_height_px=96,
        pose_long_edge_px=160,
        rotation_deg=0,
        px_per_m=100.0,
    )

    assert result.track is None
    assert result.reason is not None
    assert timestamps.size == 0
    assert summary.frames_scanned == 0
    assert summary.elapsed_ms >= 0


def test_run_ball_detection_reports_a_reason_on_a_real_clip(clip: Path) -> None:
    """A synthetic disc is not a tennis ball; the honest answer is a reason."""
    seq, _ = normalize_sequence(synthetic_sequence())

    result, summary, timestamps = run_ball_detection(
        clip,
        seq,
        contact_absolute_time_s=CONTACT_S,
        racket_wrist_index=16,
        contact_frame_index=2,
        cal_width_px=160,
        cal_height_px=96,
        pose_long_edge_px=160,
        rotation_deg=0,
        px_per_m=100.0,
        deadline_s=DETECTION_DEADLINE_S,
    )

    assert timestamps.size == summary.frames_scanned
    if result.track is None:
        assert isinstance(result.reason, BallSpeedUnavailableReason)
    else:
        assert summary.accepted_detections > 0
