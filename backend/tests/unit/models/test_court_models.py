"""Build step 1: the court contract (SESSION_MODE_PHASE2.md section 7 + brief G7-G20).

Every JSON sample below is copied VERBATIM from section 7 (and 9.1 for the
Gemini payload); the only edit is the ScoreEventRequest sample, whose
``"2d2a..."`` is already a legal free-string id under G17.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.models.court import (
    BounceSigma,
    CalibrationRejection,
    CourtAnalysisResponse,
    CourtCalibrationRequest,
    CourtCalibrationResponse,
    CourtFeedbackInput,
    CourtFeedbackZone,
    CourtKeypoint,
    CourtLine,
    LineCallTarget,
    LineVerdict,
    RallyResponse,
    ScoreConfigRequest,
    ScoreEventRequest,
    ScoreResponse,
    ScoreStateResponse,
)

CALIBRATION_REQUEST = """
{
  "frame_time_s": 2.0,
  "frame_width_px": 1920,
  "frame_height_px": 1080,
  "court_format": "singles",
  "taps": [
    {"keypoint": "near_left_singles_corner", "x": 0.12, "y": 0.91},
    {"keypoint": "near_right_singles_corner", "x": 0.88, "y": 0.91},
    {"keypoint": "far_left_singles_corner", "x": 0.41, "y": 0.28},
    {"keypoint": "far_right_singles_corner", "x": 0.59, "y": 0.28},
    {"keypoint": "near_service_t", "x": 0.50, "y": 0.58},
    {"keypoint": "far_service_t", "x": 0.50, "y": 0.34}
  ]
}
"""

CALIBRATION_RESPONSE = """
{
  "accepted": true,
  "rejection_reason": null,
  "calls_allowed": true,
  "reprojection_rms_px": 1.1,
  "reprojection_max_px": 2.3,
  "camera_height_m": 3.4,
  "overlay": {
    "lines": [{"line": "far_baseline", "a": [0.38, 0.27], "b": [0.62, 0.27]}]
  },
  "flags": [],
  "calibration_version": "court_cal_v1"
}
"""

ANALYSIS_RESPONSE = """
{
  "status": "partial",
  "refusal_reason": null,
  "court_engine_version": "court_engine_v1",
  "is_estimate": true,
  "camera": {
    "verdict": "shaky",
    "p95_shift_fraction": 0.0014,
    "rms_shift_px": 1.2,
    "bumped_at_s": null
  },
  "capture": {"fps": 59.9, "frame_width_px": 1920, "frame_height_px": 1080},
  "summary": {
    "count": 212,
    "verdict_counts": {"in": 151, "out": 18, "too_close_to_call": 43},
    "depth_expected_counts": {"short": 40.2, "mid": 88.1, "deep": 71.4, "beyond": 12.3},
    "width_counts": {"left": 61, "centre": 90, "right": 61},
    "median_sigma_along_m": 0.21
  },
  "landings": [
    {
      "bounce_id": "b_000123",
      "time_s": 212.437,
      "time_sigma_s": 0.004,
      "court_xy_m": [1.82, 10.44],
      "sigma_m": {"major": 0.31, "minor": 0.05, "angle_deg": 88.0},
      "half": "far",
      "hitter_end": "near",
      "shot_type": null,
      "is_serve": false,
      "target": "singles_half",
      "line_call": {
        "verdict": "too_close_to_call",
        "deciding_line": "far_baseline",
        "signed_margin_m": 0.21,
        "normal_sigma_m": 0.30,
        "uncertainty_radius_m": 0.59,
        "flags": []
      },
      "depth_zone": "deep",
      "depth_zone_confident": false,
      "depth_probabilities": {"short": 0.0, "mid": 0.14, "deep": 0.62, "beyond": 0.24},
      "width_zone": "right",
      "confidence": 0.72,
      "rally_id": "r_0017",
      "shot_index_in_rally": 3,
      "flags": []
    }
  ],
  "rallies": [
    {
      "rally_id": "r_0017",
      "start_s": 208.1,
      "end_s": 216.9,
      "length_hits": 5,
      "length_min": 5,
      "length_max": 6,
      "length_confidence": "medium",
      "hits_observed": 4,
      "hits_inferred": 1,
      "end_reason": "lost_track",
      "bounce_ids": ["b_000120", "b_000123"],
      "flags": []
    }
  ]
}
"""

SCORE_CONFIG = """
{"court_format": "singles", "best_of_sets": 3, "ad_scoring": true, "games_per_set": 6,
 "tiebreak_at_games": 6, "final_set": "tiebreak", "first_server": "player_a",
 "player_a_end_at_start": "near", "opponent_acknowledged": true}
"""

SCORE_RESPONSE = """
{
  "status": "estimate",
  "needs_review_count": 3,
  "score_rules_version": "score_rules_v1",
  "current": {
    "sets": [[6, 4]], "games": [2, 1], "points_display": "30-15",
    "in_tiebreak": false, "server": "player_b", "serve_side": "ad",
    "player_a_end": "far", "match_winner": null, "unresolved_before": 2
  },
  "timeline": [
    {"event_id": "se_0091", "rally_id": "r_0017", "kind": "point_unresolved",
     "winner": null, "source": "inferred", "basis": "lost_track",
     "needs_review": true, "points_display": "30-15"}
  ]
}
"""

SCORE_EVENT = """
{"client_event_id": "2d2a...", "kind": "point_won", "winner": "player_a",
 "rally_id": "r_0017", "supersedes": "se_0091"}
"""

FEEDBACK_INPUT = """
{
  "court_engine_version": "court_engine_v1",
  "is_estimate": true,
  "data_quality": "limited",
  "landings_counted": 212,
  "line_calls": {"in": 151, "out": 18, "too_close": 43,
                 "in_percent": 71, "out_percent": 8, "too_close_percent": 20},
  "depth": [{"zone": "deep", "expected_percent": 34}],
  "width": [{"zone": "right", "percent": 29}],
  "serves": {"in": 30, "out": 12, "too_close": 9},
  "rally": {"rallies": 42, "median_hits": 4, "longest_hits": 11,
            "low_confidence_rallies": 9},
  "score": {"included": false, "reason": "estimate_not_confirmed"},
  "ball_speed": {"mph": null, "confidence": "unavailable", "reason": "not_measured_in_court_mode"}
}
"""

SAMPLES: list[tuple[type[BaseModel], str]] = [
    (CourtCalibrationRequest, CALIBRATION_REQUEST),
    (CourtCalibrationResponse, CALIBRATION_RESPONSE),
    (CourtAnalysisResponse, ANALYSIS_RESPONSE),
    (ScoreConfigRequest, SCORE_CONFIG),
    (ScoreResponse, SCORE_RESPONSE),
    (ScoreEventRequest, SCORE_EVENT),
    (CourtFeedbackInput, FEEDBACK_INPUT),
]


def _ids() -> list[str]:
    return [model.__name__ for model, _ in SAMPLES]


@pytest.mark.parametrize(("model", "sample"), SAMPLES, ids=_ids())
def test_every_section_7_sample_parses_verbatim(model: type[BaseModel], sample: str) -> None:
    model.model_validate_json(sample)


@pytest.mark.parametrize(("model", "sample"), SAMPLES, ids=_ids())
def test_every_sample_round_trips_to_the_same_json(model: type[BaseModel], sample: str) -> None:
    parsed = model.model_validate_json(sample)
    dumped = json.loads(parsed.model_dump_json(by_alias=True, exclude_unset=True))
    assert dumped == json.loads(sample)


def _nested_targets(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every JSON object in a sample, so ``extra`` is tried at each level."""
    found: list[dict[str, Any]] = [document]
    for value in document.values():
        if isinstance(value, dict):
            found.extend(_nested_targets(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found.extend(_nested_targets(item))
    return found


@pytest.mark.parametrize(("model", "sample"), SAMPLES, ids=_ids())
def test_extra_field_is_rejected_at_every_nesting_level(
    model: type[BaseModel], sample: str
) -> None:
    # Models forbid extras; the enum-keyed count maps reject a non-enum key too.
    count = len(_nested_targets(json.loads(sample)))
    for index in range(count):
        document = json.loads(sample)
        _nested_targets(document)[index]["unexpected_field"] = 1
        with pytest.raises(ValidationError):
            model.model_validate(document)


def test_verdict_is_a_closed_set_of_three() -> None:
    assert {v.value for v in LineVerdict} == {"in", "out", "too_close_to_call"}
    document = json.loads(ANALYSIS_RESPONSE)
    document["landings"][0]["line_call"]["verdict"] = "probably_in"
    with pytest.raises(ValidationError):
        CourtAnalysisResponse.model_validate(document)


def test_null_is_not_coerced_to_zero() -> None:
    response = CourtAnalysisResponse.model_validate_json(ANALYSIS_RESPONSE)
    assert response.camera.bumped_at_s is None
    assert response.landings[0].shot_type is None
    score = ScoreResponse.model_validate_json(SCORE_RESPONSE)
    assert score.current.match_winner is None
    assert score.timeline[0].winner is None
    feedback = CourtFeedbackInput.model_validate_json(FEEDBACK_INPUT)
    assert feedback.ball_speed.mph is None

    document = json.loads(ANALYSIS_RESPONSE)
    document["summary"]["median_sigma_along_m"] = None
    assert CourtAnalysisResponse.model_validate(document).summary.median_sigma_along_m is None


def test_court_mode_ball_speed_can_never_carry_a_number() -> None:
    document = json.loads(FEEDBACK_INPUT)
    document["ball_speed"]["mph"] = 0
    with pytest.raises(ValidationError):
        CourtFeedbackInput.model_validate(document)


def test_g19_wire_names() -> None:
    assert len(CourtKeypoint) == 14
    expected_keypoints = {
        f"{end}_{side}_{kind}_corner"
        for end in ("near", "far")
        for side in ("left", "right")
        for kind in ("doubles", "singles", "service")
    } | {"near_service_t", "far_service_t"}
    assert {k.value for k in CourtKeypoint} == expected_keypoints
    assert {line.value for line in CourtLine} == {
        "near_baseline",
        "far_baseline",
        "near_service_line",
        "far_service_line",
        "left_singles_sideline",
        "right_singles_sideline",
        "left_doubles_sideline",
        "right_doubles_sideline",
        "centre_service_line",
    }


def test_g8_targets_and_g18_rejection_reasons() -> None:
    assert {t.value for t in LineCallTarget} == {
        "singles_half",
        "doubles_half",
        "service_box_deuce",
        "service_box_ad",
        "half_only",
    }
    assert "too_few_taps" in {r.value for r in CalibrationRejection}


def test_calibration_request_does_not_reject_few_taps_itself() -> None:
    """``too_few_taps`` is a calibration verdict, not a 422."""
    document = json.loads(CALIBRATION_REQUEST)
    document["taps"] = document["taps"][:2]
    assert len(CourtCalibrationRequest.model_validate(document).taps) == 2


def test_tap_outside_the_frame_is_rejected() -> None:
    document = json.loads(CALIBRATION_REQUEST)
    document["taps"][0]["x"] = 1.2
    with pytest.raises(ValidationError):
        CourtCalibrationRequest.model_validate(document)


def test_calibration_response_accepted_must_match_rejection() -> None:
    document = json.loads(CALIBRATION_RESPONSE)
    document["rejection_reason"] = "collinear_taps"
    with pytest.raises(ValidationError):
        CourtCalibrationResponse.model_validate(document)
    document["accepted"] = False
    document["calls_allowed"] = False
    document["overlay"] = None
    rejected = CourtCalibrationResponse.model_validate(document)
    assert rejected.rejection_reason is CalibrationRejection.COLLINEAR_TAPS


def test_refused_response_carries_reason_and_no_output() -> None:
    document = json.loads(ANALYSIS_RESPONSE)
    document["status"] = "refused"
    with pytest.raises(ValidationError):  # no reason
        CourtAnalysisResponse.model_validate(document)
    document["refusal_reason"] = "camera_moving"
    with pytest.raises(ValidationError):  # still has output
        CourtAnalysisResponse.model_validate(document)
    document["landings"] = []
    document["rallies"] = []
    document["summary"] = {
        "count": 0,
        "verdict_counts": {},
        "depth_expected_counts": {},
        "width_counts": {},
        "median_sigma_along_m": None,
    }
    refused = CourtAnalysisResponse.model_validate(document)
    assert refused.summary.median_sigma_along_m is None


def test_unknown_refusal_reason_is_rejected() -> None:
    document = json.loads(ANALYSIS_RESPONSE)
    document["refusal_reason"] = "too_dark"
    with pytest.raises(ValidationError):
        CourtAnalysisResponse.model_validate(document)


def test_landing_without_a_call_and_with_a_hitter() -> None:
    """Shot-map-only calibration has no line call; G18 adds nullable ``hitter``."""
    document = json.loads(ANALYSIS_RESPONSE)
    landing = document["landings"][0]
    landing["line_call"] = None
    landing["hitter"] = "player_a"
    landing["rally_id"] = None
    landing["shot_index_in_rally"] = None
    parsed = CourtAnalysisResponse.model_validate(document).landings[0]
    assert parsed.line_call is None
    assert parsed.hitter is not None and parsed.hitter.value == "player_a"


def test_sigma_ellipse_bounds() -> None:
    BounceSigma(major=0.3, minor=0.05, angle_deg=0.0)
    for bad in (
        {"major": 0.3, "minor": 0.05, "angle_deg": 180.0},
        {"major": 0.3, "minor": 0.05, "angle_deg": -1.0},
        {"major": 0.05, "minor": 0.3, "angle_deg": 10.0},
    ):
        with pytest.raises(ValidationError):
            BounceSigma.model_validate(bad)


def test_rally_length_invariants() -> None:
    rally = json.loads(ANALYSIS_RESPONSE)["rallies"][0]
    RallyResponse.model_validate(rally)
    for change in (
        {"length_hits": 7},  # above max
        {"length_hits": 4},  # below min
        {"hits_inferred": 0},  # observed + inferred != min
        {"end_s": 100.0},  # before start
    ):
        with pytest.raises(ValidationError):
            RallyResponse.model_validate({**rally, **change})


def test_score_state_point_labels_g14() -> None:
    state = json.loads(SCORE_RESPONSE)["current"]
    assert ScoreStateResponse.model_validate(state).point_labels is None
    state["point_labels"] = ["30", "15"]
    assert ScoreStateResponse.model_validate(state).point_labels == ("30", "15")


def test_score_status_is_estimate_or_confirmed() -> None:
    document = json.loads(SCORE_RESPONSE)
    document["status"] = "final"
    with pytest.raises(ValidationError):
        ScoreResponse.model_validate(document)


def test_score_event_client_id_is_a_bounded_free_string_g17() -> None:
    document = json.loads(SCORE_EVENT)
    for bad in ("", "x" * 65):
        with pytest.raises(ValidationError):
            ScoreEventRequest.model_validate({**document, "client_event_id": bad})
    ScoreEventRequest.model_validate({**document, "client_event_id": "x" * 64})


def test_score_event_kind_and_winner() -> None:
    document = json.loads(SCORE_EVENT)
    with pytest.raises(ValidationError):  # users never post an unresolved point
        ScoreEventRequest.model_validate({**document, "kind": "point_unresolved"})
    with pytest.raises(ValidationError):  # point_won without a winner
        ScoreEventRequest.model_validate({**document, "winner": None})
    with pytest.raises(ValidationError):  # void with a winner
        ScoreEventRequest.model_validate({**document, "kind": "point_void"})
    ScoreEventRequest.model_validate({**document, "kind": "point_void", "winner": None})


def test_score_config_best_of_is_closed() -> None:
    document = json.loads(SCORE_CONFIG)
    with pytest.raises(ValidationError):
        ScoreConfigRequest.model_validate({**document, "best_of_sets": 2})


def test_feedback_zone_carries_exactly_one_percent() -> None:
    with pytest.raises(ValidationError):
        CourtFeedbackZone.model_validate({"zone": "deep"})
    with pytest.raises(ValidationError):
        CourtFeedbackZone.model_validate({"zone": "deep", "expected_percent": 3, "percent": 3})


def test_feedback_data_quality_is_closed() -> None:
    document = json.loads(FEEDBACK_INPUT)
    document["data_quality"] = "shaky"
    with pytest.raises(ValidationError):
        CourtFeedbackInput.model_validate(document)
