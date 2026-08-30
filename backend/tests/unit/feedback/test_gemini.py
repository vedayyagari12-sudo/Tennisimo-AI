"""Unit tests for app/feedback/gemini.py. The Gemini client is always mocked."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.feedback.gemini import (
    GUARD_FALLBACK_MESSAGE,
    LOW_CONFIDENCE_FALLBACK_MESSAGE,
    MIN_CONTACT_CONFIDENCE_FOR_GEMINI,
    MODEL_NAME,
    SYSTEM_INSTRUCTION,
    build_feedback_payload,
    generate_feedback,
    generation_config,
    hedge_for,
    numeric_allowlist,
    parse_gemini_response,
    render_user_prompt,
    response_schema,
    should_skip_gemini,
)
from app.models.enums import BallSpeedConfidence, FeedbackSource
from app.models.feedback import FeedbackInput
from tests.conftest import FakeGeminiClient, make_core

VALID_DRAFT: dict[str, Any] = {
    "summary": "Good topspin shape overall, with a 71.5 score and a strike at 68 mph.",
    "strengths": [
        "Contact height sat in the ideal band at 0.9 on the hip-to-shoulder scale."
    ],
    "improvements": [
        {
            "priority": 1,
            "title": "Increase shoulder-hip separation",
            "why": "Separation measured 38.4 deg against an ideal floor of 45.0 deg.",
            "cue": "Turn the shoulders more than the hips.",
            "drill": "Shadow swings holding the racket across the shoulders.",
            "metric_refs": ["shoulder_hip_separation_deg"],
        }
    ],
}


def _raw(draft: dict[str, Any]) -> str:
    return json.dumps(draft)


# --------------------------------------------------------------------------- #
# Payload
# --------------------------------------------------------------------------- #


def test_payload_carries_numbers_enums_and_verdicts_only(
    core_with_speed: FeedbackInput,
) -> None:
    payload = build_feedback_payload(core_with_speed)
    assert payload["shot_type"] == "forehand_topspin"
    assert payload["overall_score"] == 71.5
    assert payload["unavailable_metrics"] == ["knee_flexion_min_deg"]
    # A None metric stays None. It is never defaulted and never zero.
    unavailable = [m for m in payload["metrics"] if m["name"] == "knee_flexion_min_deg"]
    assert unavailable[0]["value"] is None
    assert unavailable[0]["score"] is None
    assert unavailable[0]["verdict"] == "unavailable"


def test_prompt_leaks_no_video_identity_or_calibration_data(
    core_with_speed: FeedbackInput,
) -> None:
    prompt = render_user_prompt(build_feedback_payload(core_with_speed)).lower()
    for forbidden in (
        "email",
        "user_id",
        "landmark",
        "keypoint",
        "frame_",
        "storage_path",
        "point_a",
        "point_b",
        "px_per_m",
        "base64",
        "image",
        "video",
    ):
        assert forbidden not in prompt, forbidden


def test_ball_speed_block_present_when_measured(
    core_with_speed: FeedbackInput,
) -> None:
    block = build_feedback_payload(core_with_speed)["ball_speed"]
    assert block["mph"] == 68
    assert isinstance(block["mph"], int)
    assert block["confidence"] == "medium"
    assert block["hedge"] == "approximate"
    assert block["detections_used"] == 6
    assert "68" in block["reporting_rule"]


def test_ball_speed_block_is_neutered_never_omitted_when_unavailable(
    core_without_speed: FeedbackInput,
) -> None:
    payload = build_feedback_payload(core_without_speed)
    assert "ball_speed" in payload
    block = payload["ball_speed"]
    assert block["mph"] is None
    assert block["confidence"] == "unavailable"
    assert block["reason"] == "not_calibrated"
    assert "mph" in block["reporting_rule"]
    assert "hedge" not in block


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (BallSpeedConfidence.HIGH, "measured"),
        (BallSpeedConfidence.MEDIUM, "approximate"),
        (BallSpeedConfidence.LOW, "rough"),
        (BallSpeedConfidence.UNAVAILABLE, "none"),
    ],
)
def test_hedge_mapping_is_applied_in_python(
    confidence: BallSpeedConfidence, expected: str
) -> None:
    assert hedge_for(confidence) == expected


def test_allowlist_adds_ball_speed_as_integer_only(
    core_with_speed: FeedbackInput,
) -> None:
    allowlist = numeric_allowlist(build_feedback_payload(core_with_speed))
    assert "68" in allowlist
    assert "68.0" not in allowlist
    assert "6" in allowlist
    assert "6.0" not in allowlist
    assert "71.5" in allowlist
    assert {"1", "2", "3", "4", "5"} <= allowlist


def test_generation_config_matches_the_contract() -> None:
    config = generation_config()
    assert config["temperature"] == 0.3
    assert config["max_output_tokens"] == 800
    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"] == response_schema()
    assert config["response_schema"]["required"] == [
        "summary",
        "strengths",
        "improvements",
    ]


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #


def test_parse_bare_json() -> None:
    draft = parse_gemini_response(_raw(VALID_DRAFT))
    assert draft is not None
    assert draft.summary == VALID_DRAFT["summary"]


@pytest.mark.parametrize("fence", ["```json", "```JSON", "```"])
def test_parse_fenced_json(fence: str) -> None:
    draft = parse_gemini_response(f"{fence}\n{_raw(VALID_DRAFT)}\n```")
    assert draft is not None
    assert len(draft.improvements) == 1


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not json at all", "{", '{"summary": }', "[1, 2, 3]", '"a string"'],
)
def test_parse_malformed_returns_none_and_never_raises(raw: str) -> None:
    assert parse_gemini_response(raw) is None


def test_parse_schema_mismatch_returns_none() -> None:
    assert parse_gemini_response('{"nope": 1}') is None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def test_valid_response_produces_gemini_sourced_feedback(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=_raw(VALID_DRAFT))
    feedback = generate_feedback(core_with_speed, client)
    assert client.call_count == 1
    assert feedback.source is FeedbackSource.GEMINI
    assert feedback.model == MODEL_NAME
    assert feedback.guard.passed is True
    assert feedback.guard.mph_rule == "bound_to:68"
    assert feedback.summary == VALID_DRAFT["summary"]
    assert len(feedback.improvements) == 1


def test_call_uses_system_instruction_and_config(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=_raw(VALID_DRAFT))
    generate_feedback(core_with_speed, client)
    call = client.calls[0]
    assert call["system_instruction"] == SYSTEM_INSTRUCTION
    assert call["config"] == generation_config()
    assert "forehand_topspin" in call["prompt"]


def test_fenced_response_is_accepted_end_to_end(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=f"```json\n{_raw(VALID_DRAFT)}\n```")
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.GEMINI
    assert feedback.summary == VALID_DRAFT["summary"]


def test_malformed_response_falls_back_without_raising(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response="sorry, I can't do that")
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.summary == GUARD_FALLBACK_MESSAGE
    assert feedback.model is None
    assert feedback.guard.fell_back_to_template is True


def test_client_exception_falls_back_without_raising(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(error=TimeoutError("upstream timeout"))
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.summary == GUARD_FALLBACK_MESSAGE


def test_rogue_number_in_a_strength_discards_only_that_field(
    core_with_speed: FeedbackInput,
) -> None:
    draft = json.loads(_raw(VALID_DRAFT))
    draft["strengths"].append("Your racket head reached 92.7 on the swing.")
    client = FakeGeminiClient(response=json.dumps(draft))
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.GEMINI_PARTIAL
    assert feedback.guard.fields_discarded == ["strengths[1]"]
    assert "92.7" in feedback.guard.rejected_tokens
    assert len(feedback.strengths) == 1


def test_banned_unit_in_summary_forces_full_fallback(
    core_with_speed: FeedbackInput,
) -> None:
    draft = json.loads(_raw(VALID_DRAFT))
    draft["summary"] = "Nice swing; your hand travelled several feet after contact."
    client = FakeGeminiClient(response=json.dumps(draft))
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.TEMPLATE
    assert "feet" in feedback.guard.rejected_tokens
    assert feedback.guard.fields_discarded == ["summary"]


# --------------------------------------------------------------------------- #
# Skip path -- no network call at all
# --------------------------------------------------------------------------- #


def test_should_skip_flags() -> None:
    assert should_skip_gemini(make_core(contact_confidence=0.2)) is True
    assert should_skip_gemini(make_core(truncated_clip=True)) is True
    assert should_skip_gemini(make_core(contact_confidence=0.9)) is False
    below = MIN_CONTACT_CONFIDENCE_FOR_GEMINI - 0.01
    assert should_skip_gemini(make_core(contact_confidence=below)) is True
    at = MIN_CONTACT_CONFIDENCE_FOR_GEMINI
    assert should_skip_gemini(make_core(contact_confidence=at)) is False


@pytest.mark.parametrize(
    "core",
    [
        make_core(contact_confidence=0.20),
        make_core(truncated_clip=True),
        make_core(contact_confidence=0.20, truncated_clip=True),
        make_core(ball_speed_mph=None, contact_confidence=0.10),
    ],
    ids=["low_confidence", "truncated", "both", "low_conf_no_speed"],
)
def test_low_confidence_or_truncated_skips_gemini_entirely(
    core: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=_raw(VALID_DRAFT))
    feedback = generate_feedback(core, client)
    assert client.call_count == 0, "Gemini must not be invoked on the skip path"
    assert feedback.summary == LOW_CONFIDENCE_FALLBACK_MESSAGE
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.model is None
    assert feedback.strengths == []
    assert feedback.improvements == []
    assert feedback.guard.fell_back_to_template is True
