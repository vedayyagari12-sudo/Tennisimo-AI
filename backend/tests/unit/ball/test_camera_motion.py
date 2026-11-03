"""Build step 8: ``app/ball/camera_motion.py`` (design 2.4).

Done when: synthetic pan, shake and bump are classified correctly, and moving
players do not trigger ``moving``. Frames are a seeded texture translated by a
known sub-pixel offset per frame; players are dark rectangles moving over it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from app.analysis.camera_stability import StabilityVerdict, assess_camera_stability
from app.ball.camera_motion import (
    CameraMotionSampler,
    CameraMotionSamples,
    median_feature_shift,
    reference_features,
)

U8 = npt.NDArray[np.uint8]
SEED = 20261102
WIDTH, HEIGHT = 1920, 1080
MARGIN = 40
FPS = 10.0
DURATION_S = 12.0


def texture(seed: int = SEED) -> U8:
    rng = np.random.default_rng(seed)
    field = rng.random((HEIGHT + 2 * MARGIN, WIDTH + 2 * MARGIN)).astype(np.float32)
    field = cv2.GaussianBlur(field, (0, 0), 3.0)
    field = (field - field.min()) / (field.max() - field.min()) * 255.0
    return np.asarray(field.astype(np.uint8))


TEXTURE = texture()


def frame_at(offset: tuple[float, float], players: Sequence[tuple[int, int, int, int]] = ()) -> U8:
    """The texture moved by ``offset`` (camera motion moves the image), cropped."""
    matrix = np.array([[1.0, 0.0, offset[0] - MARGIN], [0.0, 1.0, offset[1] - MARGIN]])
    grey = cv2.warpAffine(TEXTURE, matrix, (WIDTH, HEIGHT), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    for x0, y0, x1, y1 in players:
        grey[y0:y1, x0:x1] = 20
    return np.asarray(cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR))


def run(offset_at: Callable[[float], tuple[float, float]], players_at: Callable[[float], list[tuple[int, int, int, int]]] | None = None) -> CameraMotionSamples:
    sampler = CameraMotionSampler()
    for index in range(int(DURATION_S * FPS) + 1):
        t = index / FPS
        sampler.add(t, frame_at(offset_at(t), [] if players_at is None else players_at(t)))
    return sampler.result()


def verdict_of(samples: CameraMotionSamples) -> tuple[StabilityVerdict, float | None]:
    result = assess_camera_stability(
        samples.times_s, samples.shifts_px, samples.feature_counts, samples.long_edge_px
    )
    return result.verdict, result.bumped_at_s


def small_grey(frame: U8) -> U8:
    small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
    return np.asarray(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))


# ------------------------------------------------------- median_feature_shift


@pytest.mark.parametrize("shift", [(0.0, 0.0), (1.0, -0.5), (3.25, 2.0), (-6.0, 4.5)])
def test_median_feature_shift_recovers_a_known_translation(shift: tuple[float, float]) -> None:
    reference = small_grey(frame_at((0.0, 0.0)))
    moved = small_grey(frame_at((shift[0] * 3.0, shift[1] * 3.0)))  # 1920 -> 640 is / 3
    points = reference_features(reference)
    assert len(points) >= 200
    dx, dy, count = median_feature_shift(reference, moved, points)
    assert count >= 0.9 * len(points)
    assert dx == pytest.approx(shift[0], abs=0.05)
    assert dy == pytest.approx(shift[1], abs=0.05)


def test_median_ignores_a_moving_player_on_a_minority_of_features() -> None:
    reference_frame = frame_at((0.0, 0.0), [(300, 300, 700, 900)])
    later_frame = frame_at((0.0, 0.0), [(900, 300, 1300, 900)])
    reference = small_grey(reference_frame)
    points = reference_features(reference)
    dx, dy, _ = median_feature_shift(reference, small_grey(later_frame), points)
    assert abs(dx) < 0.05 and abs(dy) < 0.05


def test_featureless_frame_has_no_features_and_nan_shift() -> None:
    flat = np.full((360, 640), 128, dtype=np.uint8)
    points = reference_features(flat)
    assert points.shape == (0, 1, 2)
    dx, dy, count = median_feature_shift(flat, flat, points)
    assert np.isnan(dx) and np.isnan(dy) and count == 0


# ------------------------------------------------------------------ sampler


def test_sampler_takes_2_hz_from_real_times_and_scales_to_input_pixels() -> None:
    samples = run(lambda t: (3.0, -1.5))  # constant: frame 0 already shifted
    assert np.allclose(samples.times_s, np.arange(0.0, DURATION_S + 1e-9, 0.5))
    assert samples.long_edge_px == WIDTH
    assert np.allclose(samples.shifts_px, 0.0, atol=0.15)  # relative to the first sample
    late = run(lambda t: (0.0, 0.0) if t < 1.0 else (6.0, -3.0))
    assert np.allclose(late.shifts_px[late.times_s >= 1.0], (6.0, -3.0), atol=0.15)


def test_sampler_follows_irregular_frame_times() -> None:
    sampler = CameraMotionSampler()
    for t in (0.0, 0.2, 0.49, 0.53, 0.9, 1.07, 1.6, 1.61):
        sampler.add(t, frame_at((0.0, 0.0)))
    assert sampler.result().times_s.tolist() == [0.0, 0.53, 1.07, 1.6]


def test_featureless_stream_has_zero_counts() -> None:
    sampler = CameraMotionSampler()
    flat = np.full((HEIGHT, WIDTH, 3), 90, dtype=np.uint8)
    for index in range(10):
        sampler.add(index * 0.25, flat)
    samples = sampler.result()
    assert np.all(samples.feature_counts == 0)
    assert verdict_of(samples)[0] is StabilityVerdict.UNKNOWN


# ------------------------------------------------- end to end, done criteria


def test_still_camera_is_stable() -> None:
    assert verdict_of(run(lambda t: (0.0, 0.0))) == (StabilityVerdict.STABLE, None)


def test_pan_is_moving() -> None:
    assert verdict_of(run(lambda t: (4.0 * t, 0.0))) == (StabilityVerdict.MOVING, None)


def test_shake_is_shaky() -> None:
    rng = np.random.default_rng(SEED)
    jitter = rng.normal(0.0, 1.5, (int(DURATION_S * FPS) + 1, 2))
    jitter[0] = 0.0

    def offset(t: float) -> tuple[float, float]:
        row = jitter[int(round(t * FPS))]
        return float(row[0]), float(row[1])

    assert verdict_of(run(offset)) == (StabilityVerdict.SHAKY, None)


def test_bump_is_stable_until_the_bump() -> None:
    verdict, bumped = verdict_of(run(lambda t: (0.0, 0.0) if t < 7.0 else (9.0, 4.0)))
    assert verdict is StabilityVerdict.STABLE
    assert bumped == 7.0


def test_moving_players_do_not_trigger_moving() -> None:
    def players(t: float) -> list[tuple[int, int, int, int]]:
        near_x = int(200 + 120 * t)
        far_x = int(1500 - 80 * t)
        return [(near_x, 500, near_x + 260, 1050), (far_x, 150, far_x + 90, 360)]

    assert verdict_of(run(lambda t: (0.0, 0.0), players)) == (StabilityVerdict.STABLE, None)
