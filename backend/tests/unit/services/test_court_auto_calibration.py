"""Automatic court calibration in ``court_offline`` (no taps needed)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.models.court import CourtCalibrationRequest, CourtFormat, CourtKeypoint, CourtTapRequest
from app.services import court_offline
from app.services.court_offline import auto_calibration, extract_court_inputs
from tests.unit.court_synth.camera import default_camera
from tests.unit.court_synth.render import Surface, render_static_court

CAMERA = default_camera((1280, 720))


def write_clip(path: Path, frame: np.ndarray, seconds: float = 2.5, fps: float = 30.0) -> Path:
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    for _ in range(int(seconds * fps)):
        writer.write(frame)
    writer.release()
    return path


def test_a_rendered_court_calibrates_automatically_with_calls() -> None:
    verdict = auto_calibration(render_static_court(CAMERA, Surface.HARD_BLUE))
    assert verdict is not None and verdict.calibration is not None
    assert verdict.calibration.calls_allowed
    assert verdict.calibration.frame_size == CAMERA.frame_size


def test_a_low_support_automatic_fit_places_shots_but_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(court_offline, "AUTO_FIT_CALLS_MIN_SUPPORT", 1.01)  # above any support
    verdict = auto_calibration(render_static_court(CAMERA, Surface.CLAY))
    assert verdict is not None and verdict.calibration is not None
    assert not verdict.calibration.calls_allowed
    assert "auto_fit_low_support" in verdict.calibration.flags


def test_no_court_gives_no_automatic_calibration() -> None:
    assert auto_calibration(np.full((720, 1280, 3), (150, 95, 45), dtype=np.uint8)) is None


def test_a_clip_without_taps_is_calibrated_automatically(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "court.mp4", render_static_court(CAMERA, Surface.HARD_GREEN))
    inputs = extract_court_inputs(clip, None)
    assert inputs.calibration_source == "auto"
    assert inputs.calibration_verdict is not None and inputs.calibration_verdict.calibration is not None


def test_rejected_taps_fall_back_to_the_automatic_fit(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "court.mp4", render_static_court(CAMERA, Surface.HARD_GREEN))
    # Two taps cannot calibrate a court.
    taps = [
        CourtTapRequest(keypoint=CourtKeypoint.NEAR_SERVICE_T, x=0.5, y=0.6),
        CourtTapRequest(keypoint=CourtKeypoint.FAR_SERVICE_T, x=0.5, y=0.4),
    ]
    request = CourtCalibrationRequest(
        frame_time_s=0.0, frame_width_px=1280, frame_height_px=720,
        court_format=CourtFormat.SINGLES, taps=taps,
    )
    inputs = extract_court_inputs(clip, request)
    assert inputs.calibration_source == "auto"
    assert inputs.calibration_verdict is not None and inputs.calibration_verdict.calibration is not None


def test_a_courtless_clip_stays_uncalibrated(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "plain.mp4", np.full((720, 1280, 3), (150, 95, 45), dtype=np.uint8))
    inputs = extract_court_inputs(clip, None)
    assert inputs.calibration_source is None
    assert inputs.calibration_verdict is None
