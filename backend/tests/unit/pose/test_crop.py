"""``app/pose/crop.py``: the zoom-in retry's pure parts."""

from __future__ import annotations

import numpy as np
import pytest

from app.pose.crop import (
    BOX_MIN_HEIGHT,
    box_area,
    crop_pixels,
    landmark_box,
    motion_box,
    uncrop_landmarks,
    worth_cropping,
)


def landmarks_at(x0: float, y0: float, x1: float, y1: float, frames: int = 4) -> np.ndarray:
    data = np.zeros((frames, 33, 4), dtype=np.float32)
    data[..., 0] = np.linspace(x0, x1, 33)
    data[..., 1] = np.linspace(y0, y1, 33)
    data[..., 3] = 0.9
    return data


def test_landmark_box_surrounds_the_player_with_a_margin() -> None:
    box = landmark_box(landmarks_at(0.40, 0.50, 0.44, 0.70), np.ones(4, dtype=bool))
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 < 0.40 and x1 > 0.44 and y0 < 0.50 and y1 > 0.70
    assert y1 - y0 >= BOX_MIN_HEIGHT - 1e-9
    assert worth_cropping(box)


def test_landmark_box_ignores_undetected_frames_and_unseen_points() -> None:
    data = landmarks_at(0.40, 0.50, 0.44, 0.70)
    data[1, :, 0] = 0.95  # a frame that was not detected
    data[0, 0, :2] = (0.01, 0.01)
    data[0, 0, 3] = 0.05  # not visible
    detected = np.array([True, False, True, True])
    box = landmark_box(data, detected)
    assert box is not None and box[0] > 0.2 and box[2] < 0.7


def test_no_detection_gives_no_landmark_box() -> None:
    assert landmark_box(landmarks_at(0.4, 0.5, 0.44, 0.7), np.zeros(4, dtype=bool)) is None
    assert landmark_box(np.zeros((0, 33, 4)), np.zeros(0, dtype=bool)) is None


def test_motion_box_finds_the_moving_region() -> None:
    energy = np.full((180, 320), 1.0, dtype=np.float32)
    energy[100:140, 200:215] = 40.0  # a small swinging player
    box = motion_box(energy)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 < 200 / 320 < 215 / 320 < x1
    assert y0 < 100 / 180 < 140 / 180 < y1
    assert worth_cropping(box)


def test_a_still_picture_gives_no_motion_box() -> None:
    assert motion_box(np.full((180, 320), 2.0, dtype=np.float32)) is None
    assert motion_box(np.zeros((0, 0), dtype=np.float32)) is None


def test_a_box_covering_most_of_the_frame_is_not_worth_cropping() -> None:
    assert not worth_cropping((0.0, 0.0, 0.9, 0.9))
    assert not worth_cropping(None)
    assert worth_cropping((0.3, 0.3, 0.6, 0.7))
    assert box_area((0.3, 0.3, 0.6, 0.7)) == pytest.approx(0.12)


def test_uncrop_maps_crop_fractions_back_to_the_frame() -> None:
    box = (0.25, 0.5, 0.75, 1.0)
    crop = np.zeros((1, 33, 4), dtype=np.float32)
    crop[0, :, 0] = 0.5
    crop[0, :, 1] = 0.0
    crop[0, :, 2] = 0.2
    crop[0, :, 3] = 0.8
    full = uncrop_landmarks(crop, box)
    assert full[0, 0, 0] == pytest.approx(0.5)
    assert full[0, 0, 1] == pytest.approx(0.5)
    assert full[0, 0, 2] == pytest.approx(0.1)
    assert full[0, 0, 3] == pytest.approx(0.8)


def test_crop_then_uncrop_puts_a_pixel_back_where_it_was() -> None:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[200, 400] = 255
    box = (0.5, 0.4, 0.75, 0.8)
    crop = crop_pixels(frame, box)
    ys, xs = np.nonzero(crop[..., 0])
    point = np.zeros((1, 33, 4), dtype=np.float32)
    point[0, 0, 0] = (xs[0] + 0.5) / crop.shape[1]
    point[0, 0, 1] = (ys[0] + 0.5) / crop.shape[0]
    full = uncrop_landmarks(point, box)
    assert full[0, 0, 0] * 640 == pytest.approx(400.5, abs=1.0)
    assert full[0, 0, 1] * 360 == pytest.approx(200.5, abs=1.0)
