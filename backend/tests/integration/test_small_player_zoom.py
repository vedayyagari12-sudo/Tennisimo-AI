"""Flexible capture: a small player is zoomed in on, not refused (2026-11-09).

SLOW (real decode, real MediaPipe). The vendored real serve is shrunk into a
plain full-size frame, the way a player looks when they film themselves from
a fence. Measured before the zoom-in retry: at 25 % the job failed
``pose_quality_too_low``, at 15 % ``no_pose_detected`` -- the two commonest
production refusals (25 of 65 jobs, 2026-10-24 to 10-30).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import Settings
from app.models.responses import AnalysisResponse
from app.services import pipeline as pipeline_module
from app.services.orchestrator import JobFailure
from app.services.pipeline import ZOOMED_POSE_FLAG, run_analysis_job
from tests.integration.test_pipeline_end_to_end import (
    CLIP,
    CONTACT_TOLERANCE_S,
    GROUND_TRUTH_CONTACT_S,
    RUN_ENV,
    ClipStorage,
    RecordingRepository,
    ReplayGeminiClient,
    make_context,
)

pytestmark = pytest.mark.slow


def shrink_clip(destination: Path, scale: float) -> Path:
    """The vendored clip, scaled by ``scale`` into a plain frame of its own
    size, placed low and right of centre."""
    capture = cv2.VideoCapture(str(CLIP))
    fps = capture.get(cv2.CAP_PROP_FPS)
    ok, frame = capture.read()
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    while ok:
        canvas = np.full_like(frame, 60)
        small = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
        y0, x0 = int(height * 0.55), int(width * 0.6)
        canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small[: height - y0, : width - x0]
        writer.write(canvas)
        ok, frame = capture.read()
    writer.release()
    capture.release()
    return destination


class ShrunkStorage(ClipStorage):
    def __init__(self, clip: Path) -> None:
        super().__init__()
        self.clip = clip

    async def download_to_path(self, storage_path: str, destination: Path) -> int:
        shutil.copyfile(self.clip, destination)
        return destination.stat().st_size


def run(clip: Path) -> AnalysisResponse:
    repository = RecordingRepository()
    context = make_context(repository, ShrunkStorage(clip), Settings(env=RUN_ENV))
    run_analysis_job(context, gemini_client=ReplayGeminiClient())
    return AnalysisResponse.model_validate(repository.rows[0].payload)


@pytest.mark.parametrize("scale", [0.25, 0.15])
def test_a_small_player_is_zoomed_in_on_and_analysed(tmp_path: Path, scale: float) -> None:
    response = run(shrink_clip(tmp_path / "small.mp4", scale))
    assert ZOOMED_POSE_FLAG in response.pose_quality.flags
    assert any("zoomed in" in w for w in response.warnings)
    earliest = GROUND_TRUTH_CONTACT_S[0] - CONTACT_TOLERANCE_S
    latest = GROUND_TRUTH_CONTACT_S[1] + CONTACT_TOLERANCE_S
    assert earliest <= response.contact.contact_absolute_time_s <= latest


@pytest.mark.parametrize("scale", [0.25, 0.15])
def test_without_the_zoom_the_same_clip_is_refused(
    tmp_path: Path, scale: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards that the test above tests the zoom, not an easy clip."""
    monkeypatch.setattr(pipeline_module, "worth_cropping", lambda box: False)
    with pytest.raises(JobFailure):
        run(shrink_clip(tmp_path / "small.mp4", scale))
