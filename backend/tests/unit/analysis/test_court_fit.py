"""Build step 19: ``analysis/court_fit.py`` (pure). Segments are built by
projecting the court model, so no pixels are needed here; rendered courts are
in ``tests/unit/ball/test_court_lines.py``."""

from __future__ import annotations

import numpy as np

from app.analysis.court_calibration import calibrate_court
from app.analysis.court_fit import COURT_FIT_VERSION, MAX_SEGMENTS, fit_court_candidates
from app.analysis.court_model import court_line_endpoints, keypoint_xy
from app.models.court import CourtLine
from tests.unit.court_synth.camera import default_camera

CAMERA = default_camera((1280, 720))


def model_segments(noise_px: float = 0.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rows = []
    for line in CourtLine:
        (x0, y0), (x1, y1) = court_line_endpoints(line)
        uv = CAMERA.project(np.array([[x0, y0, 0.0], [x1, y1, 0.0]]))
        rows.append(uv.reshape(-1))
    return np.array(rows) + rng.normal(0.0, noise_px, (len(rows), 4))


def tap_errors(segments: np.ndarray) -> list[float]:
    (best, *_rest) = fit_court_candidates(segments, CAMERA.frame_size)
    return [
        float(np.hypot(*(np.array([t.u_px, t.v_px]) - CAMERA.project(np.array([[*keypoint_xy(t.keypoint), 0.0]]))[0])))
        for t in best.taps
    ]


def test_exact_court_lines_fit_exactly() -> None:
    assert max(tap_errors(model_segments())) < 0.5


def test_noisy_lines_still_fit_and_calibrate_with_calls() -> None:
    segments = model_segments(noise_px=1.0, seed=3)
    (best, *_rest) = fit_court_candidates(segments, CAMERA.frame_size)
    assert best.support >= 0.72 and best.lines_visible >= 6
    verdict = calibrate_court(list(best.taps), CAMERA.frame_size)
    assert verdict.calibration is not None and verdict.calibration.calls_allowed


def test_at_most_three_distinct_candidates() -> None:
    candidates = fit_court_candidates(model_segments(), CAMERA.frame_size)
    assert 1 <= len(candidates) <= 3


def test_random_segments_give_no_fit() -> None:
    rng = np.random.default_rng(11)
    width, height = CAMERA.frame_size
    segments = np.column_stack([
        rng.uniform(0, width, 300), rng.uniform(0, height, 300),
        rng.uniform(0, width, 300), rng.uniform(0, height, 300),
    ])
    assert fit_court_candidates(segments, CAMERA.frame_size) == ()


def test_too_few_lines_give_no_fit() -> None:
    assert fit_court_candidates(model_segments()[:3], CAMERA.frame_size) == ()
    assert fit_court_candidates(np.zeros((0, 4)), CAMERA.frame_size) == ()


def test_texture_is_capped_and_cannot_buy_a_fit() -> None:
    rng = np.random.default_rng(5)
    width, height = CAMERA.frame_size
    short = []
    for _ in range(3000):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        angle = rng.uniform(0, np.pi)
        short.append([x, y, x + 6 * np.cos(angle), y + 6 * np.sin(angle)])
    assert len(short) > MAX_SEGMENTS
    assert fit_court_candidates(np.array(short), CAMERA.frame_size) == ()


def test_version() -> None:
    assert COURT_FIT_VERSION == "court_fit_v1"
