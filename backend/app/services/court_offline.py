"""Court mode offline glue (SESSION_MODE_PHASE2.md build step 16). IMPURE.

Decodes a video into the pure layer's inputs, runs ``analyze_court_session``,
and projects the result onto the wire models in ``app/models/court.py``. No
routes, no DB, no Jobs: those wait on Session Mode Phase 1. The projection
functions at the bottom are pure and unit-tested without any video.

Taps arrive as 0-1 fractions of the VIDEO frame (``CourtCalibrationRequest``)
and are converted to TRACK_SPACE pixels here (brief Constants), so the
calibration, the ROI and the candidates all share one pixel space.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.ball_events import PoseHit
from app.analysis.camera_stability import CameraStability, assess_camera_stability
from app.analysis.court_calibration import (
    CALIBRATION_VERSION,
    CalibrationVerdict,
    CourtCalibration,
    CourtTap,
    calibrate_court,
)
from app.analysis.court_model import court_line_endpoints
from app.analysis.court_session import COURT_ENGINE_VERSION, CourtSessionResult, analyze_court_session
from app.analysis.homography import apply_homography
from app.analysis.line_calls import LineCall, uncertainty_ellipse
from app.analysis.placement import ShotLanding, ShotMapSummary
from app.analysis.rally import Rally
from app.ball.camera_motion import CameraMotionSampler
from app.ball.flight_detector import (
    FLIGHT_CANDIDATE_DTYPE,
    court_roi_mask,
    expected_diameter_fn,
    max_diameter_fn,
    find_flight_candidates,
)
from app.ball.flight_frames import ClipProbe, iter_track_frames, probe_court_clip
from app.models.court import (
    BounceSigma,
    CalibrationOverlay,
    CameraStabilityReport,
    CaptureInfo,
    CourtAnalysisResponse,
    CourtCalibrationRequest,
    CourtCalibrationResponse,
    CourtFormat,
    CourtLine,
    CourtSessionStatus,
    LineCallResponse,
    OverlayLine,
    RallyResponse,
    ShotLandingResponse,
    ShotMapSummaryResponse,
)


@dataclass(frozen=True, eq=False)
class CourtVideoInputs:
    """Everything the pure layer needs, decoded from one clip."""

    probe: ClipProbe
    track_size: tuple[int, int] | None
    frame_times_s: npt.NDArray[np.float64]
    candidates: npt.NDArray[np.void]
    per_frame_counts: npt.NDArray[np.int64]
    stability: CameraStability
    median_feature_count: float | None
    calibration_verdict: CalibrationVerdict | None


def taps_in_track_space(
    request: CourtCalibrationRequest, track_size: tuple[int, int]
) -> list[CourtTap]:
    """Normalised taps to TRACK_SPACE pixels (brief Constants)."""
    width, height = track_size
    return [CourtTap(t.keypoint, t.x * width, t.y * height) for t in request.taps]


def extract_court_inputs(
    video: Path, request: CourtCalibrationRequest | None
) -> CourtVideoInputs:
    """Decode ``video`` once: candidates per frame, camera motion, calibration.

    The court ROI and expected ball size are used only when the taps give an
    accepted calibration; without one, candidates are searched over the whole
    frame and the session is later refused as ``not_calibrated``.
    """
    probe = probe_court_clip(video)
    sampler = CameraMotionSampler()
    window: deque[tuple[float, npt.NDArray[np.uint8], npt.NDArray[np.uint8]]] = deque(maxlen=3)
    times: list[float] = []
    chunks: list[npt.NDArray[np.void]] = []
    per_frame: list[int] = []
    verdict: CalibrationVerdict | None = None
    roi: npt.NDArray[np.uint8] | None = None
    diameter: Callable[[float, float], float] | None = None
    largest: Callable[[float, float], float] | None = None
    track_size: tuple[int, int] | None = None

    for index, (pts_s, bgr) in enumerate(iter_track_frames(video, probe)):
        if track_size is None:
            track_size = (int(bgr.shape[1]), int(bgr.shape[0]))
            if request is not None:
                verdict = calibrate_court(
                    taps_in_track_space(request, track_size),
                    track_size,
                    video_frame_size=(request.frame_width_px, request.frame_height_px),
                )
                if verdict.calibration is not None:
                    roi = court_roi_mask(verdict.calibration, track_size)
                    diameter = expected_diameter_fn(verdict.calibration)
                    largest = max_diameter_fn(verdict.calibration)
        times.append(pts_s)
        sampler.add(pts_s, bgr)
        window.append((pts_s, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), bgr))
        if len(window) == 3:
            (_, prev_gray, _), (cur_s, cur_gray, cur_bgr), (_, next_gray, _) = window
            found = find_flight_candidates(
                prev_gray, cur_gray, next_gray, cur_bgr, index - 1, cur_s, roi, diameter, largest
            )
            chunks.append(found)
            per_frame.append(len(found))

    samples = sampler.result()
    stability = assess_camera_stability(
        samples.times_s, samples.shifts_px, samples.feature_counts, max(samples.long_edge_px, 1)
    )
    return CourtVideoInputs(
        probe=probe,
        track_size=track_size,
        frame_times_s=np.asarray(times, dtype=np.float64),
        candidates=(
            np.concatenate(chunks) if chunks else np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)
        ),
        per_frame_counts=np.asarray(per_frame, dtype=np.int64),
        stability=stability,
        median_feature_count=(
            float(np.median(samples.feature_counts)) if samples.feature_counts.size else None
        ),
        calibration_verdict=verdict,
    )


def analyze_court_video(
    video: Path,
    request: CourtCalibrationRequest | None,
    pose_hits: Sequence[PoseHit] = (),
) -> tuple[CourtVideoInputs, CourtSessionResult]:
    """Decode, then run the pure court engine. Pose hits are optional."""
    inputs = extract_court_inputs(video, request)
    calibration = (
        inputs.calibration_verdict.calibration if inputs.calibration_verdict is not None else None
    )
    court_format = request.court_format if request is not None else CourtFormat.SINGLES
    result = analyze_court_session(
        inputs.candidates,
        inputs.frame_times_s,
        calibration,
        inputs.stability,
        pose_hits,
        court_format=court_format,
        frame_size=inputs.track_size or (1, 1),
    )
    return inputs, result


# ------------------------------------------------------- wire projection (pure)


def calibration_overlay(calibration: CourtCalibration) -> CalibrationOverlay:
    """Every court line drawn back into the frame, endpoints as 0-1 fractions."""
    width, height = calibration.frame_size
    lines: list[OverlayLine] = []
    for line in CourtLine:
        a, b = court_line_endpoints(line)
        uv = apply_homography(calibration.h_court_to_image, np.array([a, b], dtype=np.float64))
        lines.append(
            OverlayLine(
                line=line,
                a=(float(uv[0, 0]) / width, float(uv[0, 1]) / height),
                b=(float(uv[1, 0]) / width, float(uv[1, 1]) / height),
            )
        )
    return CalibrationOverlay(lines=lines)


def calibration_response(verdict: CalibrationVerdict) -> CourtCalibrationResponse:
    calibration = verdict.calibration
    if calibration is None:
        return CourtCalibrationResponse(
            accepted=False,
            rejection_reason=verdict.rejection,
            calls_allowed=False,
            reprojection_rms_px=None,
            reprojection_max_px=None,
            camera_height_m=None,
            overlay=None,
            flags=[],
            calibration_version=CALIBRATION_VERSION,
        )
    return CourtCalibrationResponse(
        accepted=True,
        rejection_reason=None,
        calls_allowed=calibration.calls_allowed,
        reprojection_rms_px=float(calibration.reprojection_rms_px),
        reprojection_max_px=float(calibration.reprojection_max_px),
        camera_height_m=float(calibration.pose.height_m),
        overlay=calibration_overlay(calibration),
        flags=list(calibration.flags),
        calibration_version=calibration.version,
    )


def line_call_response(call: LineCall | None) -> LineCallResponse | None:
    if call is None:
        return None
    return LineCallResponse(
        verdict=call.verdict,
        deciding_line=call.deciding_line,
        signed_margin_m=float(call.signed_margin_m),
        normal_sigma_m=float(call.normal_sigma_m),
        uncertainty_radius_m=float(call.uncertainty_radius_m),
        flags=list(call.flags),
    )


def rally_positions(rallies: Sequence[Rally]) -> dict[str, tuple[str, int]]:
    """Bounce id -> (rally id, 1-based landing order within the rally).

    Derived here, not in the pure layer (brief G8). A landing in no rally gets
    nulls on the wire.
    """
    out: dict[str, tuple[str, int]] = {}
    for rally in rallies:
        ordered = sorted(rally.landings, key=lambda l: (l.bounce.time_s, l.bounce.event_id))
        for position, landing in enumerate(ordered, start=1):
            out[landing.bounce.event_id] = (rally.rally_id, position)
    return out


def landing_response(
    landing: ShotLanding, rally: tuple[str, int] | None
) -> ShotLandingResponse:
    major, minor, angle = uncertainty_ellipse(landing.bounce.cov_m2, z=1.0)  # brief G16
    bounce = landing.bounce
    return ShotLandingResponse(
        bounce_id=bounce.event_id,
        time_s=max(float(bounce.time_s), 0.0),
        time_sigma_s=float(bounce.time_sigma_s),
        court_xy_m=(float(bounce.court_xy_m[0]), float(bounce.court_xy_m[1])),
        sigma_m=BounceSigma(major=float(major), minor=float(minor), angle_deg=float(angle) % 180.0),
        half=bounce.half,
        hitter_end=landing.hitter_end,
        shot_type=landing.shot_type,
        is_serve=landing.is_serve,
        target=landing.target,
        line_call=line_call_response(landing.call),
        depth_zone=landing.depth_zone,
        depth_zone_confident=landing.depth_confident,
        depth_probabilities={k: float(v) for k, v in landing.depth_probabilities.items()},
        width_zone=landing.width_zone,
        confidence=float(np.clip(bounce.confidence, 0.0, 1.0)),
        rally_id=None if rally is None else rally[0],
        shot_index_in_rally=None if rally is None else rally[1],
        flags=list(bounce.flags),
    )


def summary_response(summary: ShotMapSummary) -> ShotMapSummaryResponse:
    return ShotMapSummaryResponse(
        count=summary.count,
        verdict_counts=dict(summary.verdict_counts),
        depth_expected_counts={k: float(v) for k, v in summary.depth_expected_counts.items()},
        width_counts=dict(summary.width_counts),
        median_sigma_along_m=summary.median_sigma_along_m,
    )


def rally_response(rally: Rally) -> RallyResponse:
    return RallyResponse(
        rally_id=rally.rally_id,
        start_s=max(float(rally.start_s), 0.0),
        end_s=max(float(rally.end_s), 0.0),
        length_hits=rally.length_hits,
        length_min=rally.length_min,
        length_max=rally.length_max,
        length_confidence=rally.confidence,
        hits_observed=rally.hits_observed,
        hits_inferred=rally.hits_inferred,
        end_reason=rally.end_reason,
        bounce_ids=sorted(l.bounce.event_id for l in rally.landings),
        flags=list(rally.flags),
    )


def project_court_result(
    result: CourtSessionResult, *, frame_size: tuple[int, int]
) -> CourtAnalysisResponse:
    """The wire body for one analysed court session. Every court number is an
    estimate from one fixed camera, so ``is_estimate`` is always true."""
    positions = rally_positions(result.rallies)
    width, height = frame_size
    return CourtAnalysisResponse(
        status=result.status,
        refusal_reason=result.refusal_reason,
        court_engine_version=COURT_ENGINE_VERSION,
        is_estimate=True,
        camera=CameraStabilityReport(
            verdict=result.stability.verdict,
            p95_shift_fraction=result.stability.p95_shift_fraction,
            rms_shift_px=result.stability.rms_shift_px,
            bumped_at_s=result.stability.bumped_at_s,
        ),
        capture=CaptureInfo(
            # A refused-for-frame-rate clip may have no measurable fps; the wire
            # field is positive, so report the floor of what was decoded.
            fps=result.fps if result.fps > 0.0 else 1.0,
            frame_width_px=max(int(width), 1),
            frame_height_px=max(int(height), 1),
        ),
        summary=summary_response(result.summary),
        landings=[
            landing_response(l, positions.get(l.bounce.event_id)) for l in result.landings
        ],
        rallies=[rally_response(r) for r in result.rallies],
    )


__all__ = [
    "CourtSessionStatus",
    "CourtVideoInputs",
    "analyze_court_video",
    "calibration_overlay",
    "calibration_response",
    "extract_court_inputs",
    "landing_response",
    "project_court_result",
    "rally_positions",
    "taps_in_track_space",
]
