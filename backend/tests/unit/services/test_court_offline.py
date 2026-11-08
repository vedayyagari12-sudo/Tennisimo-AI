"""Build step 16: ``app/services/court_offline.py``.

The projection is pure and tested on synthetic court sessions. The decode path
is exercised once on the vendored clip (``slow``): it must run, refuse the
handheld camera, and emit no call without taps.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.analysis.court_calibration import CalibrationVerdict, CourtTap, calibrate_court
from app.analysis.court_model import keypoint_xy
from app.models.court import (
    CalibrationRejection,
    CourtAnalysisResponse,
    CourtCalibrationRequest,
    CourtLine,
    CourtRefusalReason,
    CourtSessionStatus,
    CourtTapRequest,
)
from app.services.court_offline import (
    analyze_court_video,
    calibration_response,
    project_court_result,
    rally_positions,
    taps_in_track_space,
)
from tests.unit.analysis.synthetic_court import RECOMMENDED_SIX
from tests.unit.analysis.test_ball_events import CALIBRATION, CAMERA
from tests.unit.analysis.test_court_session import SEEDS, run

CLIP = Path(__file__).resolve().parents[2] / "fixtures" / "clips" / "serve_vertical_10340710.mp4"


@pytest.mark.parametrize("seed", SEEDS)
def test_wire_body_validates_and_round_trips(seed: int) -> None:
    _, result = run(seed)
    wire = project_court_result(result, frame_size=CAMERA.frame_size)
    assert wire.status is CourtSessionStatus.COMPLETE
    assert wire.is_estimate
    assert len(wire.landings) == len(result.landings)
    again = CourtAnalysisResponse.model_validate_json(wire.model_dump_json())
    assert again == wire


def test_every_landing_in_a_rally_has_an_index() -> None:
    _, result = run(SEEDS[0])
    wire = project_court_result(result, frame_size=CAMERA.frame_size)
    positions = rally_positions(result.rallies)
    for landing in wire.landings:
        if landing.bounce_id in positions:
            assert landing.rally_id == positions[landing.bounce_id][0]
            assert landing.shot_index_in_rally is not None and landing.shot_index_in_rally >= 1
        else:
            assert landing.rally_id is None and landing.shot_index_in_rally is None
    in_rally = [l.shot_index_in_rally for l in wire.landings if l.rally_id == "r_0000"]
    assert in_rally == list(range(1, len(in_rally) + 1))


def test_sigma_is_one_sigma_and_ordered() -> None:
    _, result = run(SEEDS[1])
    for landing in project_court_result(result, frame_size=CAMERA.frame_size).landings:
        assert landing.sigma_m.major >= landing.sigma_m.minor >= 0.0
        assert 0.0 <= landing.sigma_m.angle_deg < 180.0
        if landing.line_call is not None:
            # 1.96-sigma call radius against the 1-sigma wire ellipse.
            assert landing.line_call.uncertainty_radius_m <= 1.96 * landing.sigma_m.major + 1e-9


def test_refused_session_is_empty_on_the_wire() -> None:
    _, result = run(SEEDS[0], calibration=None)
    wire = project_court_result(result, frame_size=CAMERA.frame_size)
    assert wire.status is CourtSessionStatus.REFUSED
    assert wire.refusal_reason is CourtRefusalReason.NOT_CALIBRATED
    assert wire.landings == [] and wire.rallies == [] and wire.summary.count == 0


def test_six_taps_give_null_line_calls() -> None:
    uv = CAMERA.project(np.array([[*keypoint_xy(k), 0.0] for k in RECOMMENDED_SIX]))
    taps = [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(RECOMMENDED_SIX, uv, strict=True)]
    six = calibrate_court(taps, CAMERA.frame_size, sigma_tap_px=1.0).calibration
    assert six is not None
    _, result = run(SEEDS[0], calibration=six)
    wire = project_court_result(result, frame_size=CAMERA.frame_size)
    assert wire.landings and all(l.line_call is None for l in wire.landings)


def test_calibration_response_overlay_lands_on_the_frame() -> None:
    response = calibration_response(CalibrationVerdict(CALIBRATION, None))
    assert response.accepted and response.calls_allowed
    assert response.overlay is not None
    assert {l.line for l in response.overlay.lines} == set(CourtLine)
    near = next(l for l in response.overlay.lines if l.line is CourtLine.NEAR_BASELINE)
    for x, y in (near.a, near.b):
        assert -0.5 < x < 1.5 and 0.0 < y < 1.0


def test_rejected_calibration_response_has_no_numbers() -> None:
    response = calibration_response(CalibrationVerdict(None, CalibrationRejection.TOO_FEW_TAPS))
    assert not response.accepted and not response.calls_allowed
    assert response.overlay is None and response.reprojection_rms_px is None


def test_taps_convert_fractions_to_track_pixels() -> None:
    request = CourtCalibrationRequest(
        frame_time_s=0.0,
        frame_width_px=1280,
        frame_height_px=720,
        court_format="singles",
        taps=[CourtTapRequest(keypoint="near_service_t", x=0.5, y=0.25)],
    )
    (tap,) = taps_in_track_space(request, (1920, 1080))
    assert (tap.u_px, tap.v_px) == (960.0, 270.0)


@pytest.mark.slow
def test_vendored_clip_runs_refuses_and_makes_no_call() -> None:
    inputs, result = analyze_court_video(CLIP, None)
    assert inputs.frame_times_s.size > 0
    wire = project_court_result(result, frame_size=inputs.track_size or (1, 1))
    assert wire.status is CourtSessionStatus.REFUSED
    assert wire.landings == []
