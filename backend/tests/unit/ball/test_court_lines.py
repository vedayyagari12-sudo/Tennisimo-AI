"""Build step 19: ``ball/court_lines.py`` on rendered synthetic courts."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.analysis.court_calibration import calibrate_court
from app.analysis.court_fit import fit_court_candidates
from app.analysis.court_model import keypoint_xy
from app.ball.court_lines import (
    court_surface_mask,
    detect_line_segments,
    fit_court_from_frame,
    refine_tap_to_intersection,
    segments_on_surface,
)
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


def with_stands(image: np.ndarray) -> np.ndarray:
    """Grey seating above the court: long bright rows and steep stair rails,
    which outrank the court's own lines in the 6-longest-per-family slots."""
    out = image.copy()
    out[:180] = (90, 90, 90)
    for i in range(8):
        y = 12 + i * 20
        cv2.line(out, (0, y), (1279, y + 8), (215, 215, 215), 3)
    for i in range(8):
        x = 60 + i * 160
        cv2.line(out, (x, 0), (x + 70, 175), (215, 215, 215), 3)
    return out


def test_a_frame_that_fits_directly_fits_exactly_as_before() -> None:
    image = render_static_court(CAMERA, Surface.HARD_BLUE)
    direct = fit_court_candidates(detect_line_segments(image), CAMERA.frame_size)
    assert direct
    assert fit_court_from_frame(image) == direct


@pytest.mark.parametrize("surface", list(Surface))
def test_the_surface_stages_recover_a_court_crowded_out_by_the_stands(surface: Surface) -> None:
    image = with_stands(render_static_court(CAMERA, surface))
    assert fit_court_candidates(detect_line_segments(image), CAMERA.frame_size) == ()
    (best, *_rest) = fit_court_from_frame(image)
    errors = [float(np.hypot(*(np.array([t.u_px, t.v_px]) - truth_uv(t.keypoint)))) for t in best.taps]
    assert len(best.taps) >= 8
    assert np.median(errors) <= 3.0
    # Measured 2026-11-09: median 1.1 px; worst 9-13 px, always the near
    # singles corners 12 px from the frame edge, where only a sliver of each
    # sideline is in frame. Every tap away from the edge is within 8 px.
    inner = [e for t, e in zip(best.taps, errors, strict=True) if 30.0 <= t.u_px <= 1250.0]
    assert max(inner) <= 8.0 and max(errors) <= 16.0


def test_the_surface_covers_the_court_and_not_the_stands() -> None:
    image = with_stands(render_static_court(CAMERA, Surface.HARD_GREEN))
    surface = court_surface_mask(image, close_lines=True)
    assert surface is not None
    for keypoint in (CourtKeypoint.NEAR_SERVICE_T, CourtKeypoint.NEAR_LEFT_SINGLES_CORNER):
        u, v = truth_uv(keypoint)
        assert surface[int(v), int(u)] == 255
    assert not surface[:100].any()


def test_a_plain_frame_has_no_surface_and_no_fit() -> None:
    image = np.full((720, 1280, 3), (150, 95, 45), dtype=np.uint8)
    assert court_surface_mask(image) is None
    assert fit_court_from_frame(image) == ()


def test_only_segments_wholly_on_the_surface_are_kept() -> None:
    surface = np.zeros((100, 200), dtype=np.uint8)
    surface[50:, :] = 255
    segments = np.array([
        [10.0, 60.0, 190.0, 90.0],   # inside
        [10.0, 10.0, 190.0, 90.0],   # one end off
        [10.0, 60.0, 500.0, 90.0],   # leaves the frame: clipped to the edge, inside
    ])
    kept = segments_on_surface(segments, surface)
    assert kept.tolist() == [segments[0].tolist(), segments[2].tolist()]
    assert segments_on_surface(np.zeros((0, 4)), surface).shape == (0, 4)
