"""T2: rendered frames through the real pixel front end (design 11.1). Test-only.

Frames are generated in memory one at a time and never written. The same
``find_flight_candidates`` and ``CameraMotionSampler`` the offline glue uses
run on them, so T2 exercises everything between pixels and the pure engine.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.camera_stability import CameraStability, assess_camera_stability
from app.analysis.court_calibration import CourtCalibration
from app.ball.camera_motion import CameraMotionSampler
from app.ball.flight_detector import (
    FLIGHT_CANDIDATE_DTYPE,
    court_roi_mask,
    expected_diameter_fn,
    max_diameter_fn,
    find_flight_candidates,
)
from tests.unit.court_synth.render import CourtRenderer
from tests.unit.court_synth.scene import BallRole


@dataclass(frozen=True, eq=False)
class PixelRun:
    candidates: npt.NDArray[np.void]
    frame_times_s: npt.NDArray[np.float64]
    stability: CameraStability
    truth_uv: npt.NDArray[np.float64]  # main ball centre per frame, NaN when unseen


def run_pixels(renderer: CourtRenderer, calibration: CourtCalibration) -> PixelRun:
    roi = court_roi_mask(calibration, renderer.camera.frame_size)
    diameter = expected_diameter_fn(calibration)
    largest = max_diameter_fn(calibration)
    sampler = CameraMotionSampler()
    window: deque[tuple[float, npt.NDArray[np.uint8], npt.NDArray[np.uint8]]] = deque(maxlen=3)
    chunks: list[npt.NDArray[np.void]] = []
    main = next(b for b in renderer.scene.balls if b.role is BallRole.MAIN)
    truth = np.full((renderer.frame_count, 2), np.nan)
    width, height = renderer.camera.frame_size
    for index, (pts_s, bgr) in enumerate(renderer.iter_frames()):
        uv = renderer.ball_uv(index, main)
        if np.all(np.isfinite(uv)) and 0 <= uv[0] < width and 0 <= uv[1] < height:
            truth[index] = uv
        sampler.add(pts_s, bgr)
        window.append((pts_s, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), bgr))
        if len(window) == 3:
            (_, prev_gray, _), (cur_s, cur_gray, cur_bgr), (_, next_gray, _) = window
            chunks.append(
                find_flight_candidates(
                    prev_gray, cur_gray, next_gray, cur_bgr, index - 1, cur_s, roi, diameter, largest
                )
            )
    samples = sampler.result()
    stability = assess_camera_stability(
        samples.times_s, samples.shifts_px, samples.feature_counts, max(samples.long_edge_px, 1)
    )
    return PixelRun(
        candidates=np.concatenate(chunks) if chunks else np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE),
        frame_times_s=np.asarray(renderer.frame_times, dtype=np.float64),
        stability=stability,
        truth_uv=truth,
    )
