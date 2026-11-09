"""Build step 20: ``app/feedback/court_summary.py`` (SESSION_MODE_PHASE2.md 9).

FakeGeminiClient only, no network. ``test_gemini.py`` and ``test_guard_mph.py``
are not modified.
"""

from __future__ import annotations

import json

import pytest

from app.feedback.court_summary import (
    MIN_LANDINGS_FOR_GEMINI,
    build_court_feedback_input,
    court_numeric_allowlist,
    court_payload,
    court_template_summary,
    find_court_banned,
    generate_court_summary,
)
from app.feedback.gemini import ORDINAL_ALLOWLIST, find_banned_units, validate_text_field
from app.models.court import CourtAnalysisResponse, ScoreResponse
from app.models.enums import FeedbackSource
from tests.conftest import FakeGeminiClient

LANDING = {
    "bounce_id": "b_000123",
    "time_s": 212.437,
    "time_sigma_s": 0.004,
    "court_xy_m": [1.82, 10.44],
    "sigma_m": {"major": 0.31, "minor": 0.05, "angle_deg": 88.0},
    "half": "far",
    "hitter_end": "near",
    "shot_type": None,
    "is_serve": False,
    "target": "singles_half",
    "line_call": {
        "verdict": "too_close_to_call",
        "deciding_line": "far_baseline",
        "signed_margin_m": 0.21,
        "normal_sigma_m": 0.30,
        "uncertainty_radius_m": 0.59,
        "flags": [],
    },
    "depth_zone": "deep",
    "depth_zone_confident": False,
    "depth_probabilities": {"short": 0.0, "mid": 0.14, "deep": 0.62, "beyond": 0.24},
    "width_zone": "right",
    "confidence": 0.72,
    "rally_id": "r_0017",
    "shot_index_in_rally": 3,
    "flags": [],
}
SECTION_73 = {
    "status": "partial",
    "refusal_reason": None,
    "court_engine_version": "court_engine_v1",
    "is_estimate": True,
    "camera": {"verdict": "shaky", "p95_shift_fraction": 0.0014, "rms_shift_px": 1.2, "bumped_at_s": None},
    "capture": {"fps": 59.9, "frame_width_px": 1920, "frame_height_px": 1080},
    "summary": {
        "count": 212,
        "verdict_counts": {"in": 151, "out": 18, "too_close_to_call": 43},
        "depth_expected_counts": {"short": 40.2, "mid": 88.1, "deep": 71.4, "beyond": 12.3},
        "width_counts": {"left": 61, "centre": 90, "right": 61},
        "median_sigma_along_m": 0.21,
    },
    "landings": [LANDING, {**LANDING, "bounce_id": "b_000124", "is_serve": True}],
    "rallies": [
        {
            "rally_id": "r_0017", "start_s": 208.1, "end_s": 216.9, "length_hits": 5,
            "length_min": 5, "length_max": 6, "length_confidence": "medium",
            "hits_observed": 4, "hits_inferred": 1, "end_reason": "lost_track",
            "bounce_ids": ["b_000120", "b_000123"], "flags": [],
        },
        {
            "rally_id": "r_0018", "start_s": 220.0, "end_s": 226.0, "length_hits": 11,
            "length_min": 11, "length_max": 11, "length_confidence": "high",
            "hits_observed": 11, "hits_inferred": 0, "end_reason": "ball_out",
            "bounce_ids": [], "flags": [],
        },
    ],
}
SCORE = {
    "status": "estimate",
    "needs_review_count": 3,
    "score_rules_version": "score_rules_v1",
    "current": {
        "sets": [[6, 4]], "games": [2, 1], "points_display": "30-15", "in_tiebreak": False,
        "server": "player_b", "serve_side": "ad", "player_a_end": "far",
        "match_winner": None, "unresolved_before": 2,
    },
    "timeline": [],
}


def analysis(**changes: object) -> CourtAnalysisResponse:
    return CourtAnalysisResponse.model_validate({**SECTION_73, **changes})


def draft(summary: str) -> str:
    return json.dumps({"summary": summary, "strengths": [], "improvements": []})


# --------------------------------------------------------------- payload


def test_payload_matches_section_9_1() -> None:
    core = build_court_feedback_input(analysis())
    payload = court_payload(core)
    assert payload["line_calls"] == {
        "in": 151, "out": 18, "too_close": 43,
        "in_percent": 71, "out_percent": 8, "too_close_percent": 20,
    }
    assert {d["zone"]: d["expected_percent"] for d in payload["depth"]}["deep"] == 34
    assert {w["zone"]: w["percent"] for w in payload["width"]}["right"] == 29
    assert payload["serves"] == {"in": 0, "out": 0, "too_close": 1}
    assert payload["rally"] == {"rallies": 2, "median_hits": 5, "longest_hits": 11,
                                "low_confidence_rallies": 0}
    assert payload["score"] == {"included": False, "reason": "no_score", "sets": None, "games": None}
    assert payload["ball_speed"]["mph"] is None
    assert payload["data_quality"] == "limited"  # shaky camera


def test_payload_has_no_coordinates_times_or_sigmas() -> None:
    text = json.dumps(court_payload(build_court_feedback_input(analysis())))
    for leaked in ("10.44", "212.437", "0.59", "court_xy", "time_s", "sigma"):
        assert leaked not in text


def test_good_quality_on_a_stable_camera() -> None:
    stable = {**SECTION_73["camera"], "verdict": "stable"}  # type: ignore[dict-item]
    assert build_court_feedback_input(analysis(camera=stable)).data_quality == "good"


def test_score_only_when_every_point_confirmed() -> None:
    estimate = ScoreResponse.model_validate(SCORE)
    assert not build_court_feedback_input(analysis(), estimate).score.included
    confirmed = ScoreResponse.model_validate({**SCORE, "status": "confirmed"})
    core = build_court_feedback_input(analysis(), confirmed)
    assert core.score.included and core.score.games == (2, 1)


# ----------------------------------------------------------------- guard


def test_rally_passes_the_court_guard_and_fails_single_swing() -> None:
    text = "Your rally length"
    assert find_court_banned(text) == []
    assert validate_text_field(text, frozenset(), None) != []
    assert "rally" in find_banned_units(text)


def test_generally_passes_the_court_guard() -> None:
    assert find_court_banned("You generally hit deep.") == []


def test_in_and_out_counts_pass_the_court_guard() -> None:
    assert find_court_banned("151 in, 18 out") == []


@pytest.mark.parametrize(
    "text", ["It landed 3 m long", "about 40cm out", "more spin", "two yards", "50 mph", "5 ft"]
)
def test_distances_spin_and_speed_are_banned(text: str) -> None:
    assert find_court_banned(text)


def test_allowlist_has_no_ordinals_and_adds_percents() -> None:
    payload = court_payload(build_court_feedback_input(analysis()))
    allowed = court_numeric_allowlist(payload)
    assert "71%" in allowed and "151" in allowed
    assert "3" not in allowed  # ordinal 3 is not in this payload
    assert not ORDINAL_ALLOWLIST <= allowed


# ------------------------------------------------------------ generation


def test_gemini_text_with_payload_numbers_is_kept() -> None:
    client = FakeGeminiClient(response=draft(
        "An estimated 71% of your 212 landings were in, and your longest rally was 11 hits."
    ))
    feedback = generate_court_summary(analysis(), client)
    assert client.call_count == 1
    assert feedback.source is FeedbackSource.GEMINI
    assert "rally" in feedback.summary
    assert "State rally numbers only from" in client.calls[0]["system_instruction"]


def test_an_invented_number_falls_back_to_the_template() -> None:
    # 7 appears nowhere in the payload. (A payload number reused with the
    # wrong meaning, e.g. median_hits 5 as "5 rallies", is NOT caught: the
    # guard checks numbers, not what they are attached to.)
    client = FakeGeminiClient(response=draft("You won 7 rallies in a row."))
    feedback = generate_court_summary(analysis(), client)
    assert feedback.source is FeedbackSource.TEMPLATE
    assert "7" in feedback.guard.rejected_tokens


def test_a_distance_falls_back_to_the_template() -> None:
    client = FakeGeminiClient(response=draft("Your 151 in balls were within 1 m of the line."))
    assert generate_court_summary(analysis(), client).source is FeedbackSource.TEMPLATE


def test_too_few_landings_never_calls_the_model() -> None:
    few = {
        **SECTION_73["summary"],  # type: ignore[dict-item]
        "count": MIN_LANDINGS_FOR_GEMINI - 1,
        "verdict_counts": {"in": 20, "out": 5, "too_close_to_call": 4},
        "depth_expected_counts": {"short": 5.0, "mid": 10.0, "deep": 10.0, "beyond": 4.0},
        "width_counts": {"left": 9, "centre": 10, "right": 10},
    }
    client = FakeGeminiClient(response=draft("x"))
    feedback = generate_court_summary(analysis(summary=few), client)
    assert client.call_count == 0
    assert feedback.source is FeedbackSource.TEMPLATE


def test_refused_never_calls_the_model() -> None:
    empty = {"count": 0, "verdict_counts": {}, "depth_expected_counts": {}, "width_counts": {},
             "median_sigma_along_m": None}
    client = FakeGeminiClient(response=draft("x"))
    refused = analysis(status="refused", refusal_reason="camera_moving", summary=empty,
                       landings=[], rallies=[])
    assert generate_court_summary(refused, client).source is FeedbackSource.TEMPLATE
    assert client.call_count == 0


def test_client_error_falls_back() -> None:
    client = FakeGeminiClient(error=RuntimeError("down"))
    assert generate_court_summary(analysis(), client).source is FeedbackSource.TEMPLATE


def test_template_uses_only_payload_numbers_and_passes_its_own_guard() -> None:
    core = build_court_feedback_input(analysis())
    text = court_template_summary(core)
    assert text.startswith("Estimated from 212 landings: 151 in, 18 out and 43 too close")
    allowed = court_numeric_allowlist(court_payload(core))
    assert validate_text_field(text, allowed, None, banned_finder=find_court_banned) == []
