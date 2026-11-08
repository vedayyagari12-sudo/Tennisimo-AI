"""Build step 19: ``ball/court_lines.py`` on rendered synthetic courts."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.analysis.court_calibration import calibrate_court
from app.analysis.court_fit import fit_court_candidates
from app.analysis.court_model import keypoint_xy
from app.ball.court_lines import detect_line_segments, refine_tap_to_intersection
from app.models.court import CourtKeypoint
from tests.unit.court_synth.camera import default_camera
from tests.unit.court_synth.render import Surface, render_static_court

CAMERA = default_camera((1280, 720))


def truth_uv(keypoint: CourtKeypoint) -> np.ndarray:
    return CAMERA.project(np.array([[*keypoint_xy(keypoint), 0.0]]))[0]


@pytest.mark.parametrize("surface", list(Surface))
def test_rendered_courts_fit_and_calibrate(surface: Surface) -> None:
    """Measured 2026-11-08: worst tap 4.4-6.0 px, median 1.6-2.0 px at 720p."""
    image = render_static_court(CAMERA, surface)
    candidates = fit_court_candidates(detect_line_segments(image), CAMERA.frame_size)
    assert 1 <= len(candidates) <= 3
    best = candidates[0]
    errors = [float(np.hypot(*(np.array([t.u_px, t.v_px]) - truth_uv(t.keypoint)))) for t in best.taps]
    assert len(best.taps) >= 8
    assert np.median(errors) <= 3.0 and max(errors) <= 8.0
    verdict = calibrate_court(list(best.taps), CAMERA.frame_size)
    assert verdict.calibration is not None and verdict.calibration.calls_allowed


def test_a_plain_surface_gives_no_segments_and_no_fit() -> None:
    image = np.full((720, 1280, 3), (150, 95, 45), dtype=np.uint8)
    segments = detect_line_segments(image)
    assert len(segments) == 0
    assert fit_court_candidates(segments, (1280, 720)) == ()


def test_segments_come_back_in_frame_pixels() -> None:
    big = default_camera((1920, 1080))
    segments = detect_line_segments(render_static_court(big, Surface.HARD_BLUE))
    assert len(segments) > 0
    assert segments[:, [0, 2]].max() <= 1920 and segments[:, [1, 3]].max() <= 1080


def test_refine_snaps_a_rough_tap_to_the_corner() -> None:
    gray = cv2.cvtColor(render_static_court(CAMERA, Surface.HARD_BLUE), cv2.COLOR_BGR2GRAY)
    true = truth_uv(CourtKeypoint.NEAR_SERVICE_T)
    refined = refine_tap_to_intersection(gray, float(true[0]) + 6.0, float(true[1]) - 5.0)
    assert refined is not None
    assert np.hypot(refined[0] - true[0], refined[1] - true[1]) <= 3.0


def test_refine_refuses_where_there_is_no_crossing() -> None:
    gray = np.full((720, 1280), 90, dtype=np.uint8)
    assert refine_tap_to_intersection(gray, 640.0, 360.0) is None
