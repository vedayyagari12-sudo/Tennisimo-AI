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
    round_payload_numbers,
    should_skip_gemini,
    validate_text_field,
)
from app.analysis.rubric import RUBRIC_V1
from app.models.enums import (
    BallSpeedConfidence,
    FeedbackSource,
    MetricUnit,
    MetricVerdict,
)
from app.models.feedback import BallSpeedResult, FeedbackInput, MetricScore
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
    assert config["max_output_tokens"] == 2048
    assert config["thinking_config"] == {"thinking_budget": 0}
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


# --------------------------------------------------------------------------- #
# Regression: payload precision vs. allowlist precision
# --------------------------------------------------------------------------- #
#
# The payload used to be serialized into the prompt at FULL float precision
# while the allowlist only ever rendered a float at 0 and 1 decimal places. Any
# value with 2+ decimal digits was therefore impossible for the model to quote
# without being rejected -- it read `shoulder_turn_deg: 87.15032792538659`,
# wrote "87.15" exactly as instructed, and the guard threw the whole field away
# because the allowlist held only "87" and "87.2". Hardcoded band edges such as
# `ideal_min: 0.25` could never be allowlisted at all. That is why `source` was
# "template" on every live run.


def _high_precision_core() -> FeedbackInput:
    """A core whose metric value carries far more precision than the guard renders."""
    return FeedbackInput(
        shot_type="forehand_topspin",
        shot_type_confidence=0.81,
        shot_type_evidence=[],
        handedness="right",
        contact_confidence=0.74,
        overall_score=71.5,
        category_scores={"preparation": 78.0},
        metrics=[
            MetricScore(
                name="shoulder_turn_deg",
                value=87.15032792538659,
                unit=MetricUnit.DEGREES,
                verdict=MetricVerdict.LOW,
                score=88.33333333333333,
                ideal_min=0.25,
                ideal_max=110.0,
                view_sensitive=False,
            )
        ],
        ball_speed=BallSpeedResult(
            ball_speed_mph=68,
            confidence=BallSpeedConfidence.MEDIUM,
            detections_used=6,
        ),
        priority_metric_names=["shoulder_turn_deg"],
    )


def test_prompt_never_shows_a_number_the_allowlist_rejects() -> None:
    payload = build_feedback_payload(_high_precision_core())
    allowlist = numeric_allowlist(payload)
    prompt = render_user_prompt(payload)

    # What the model is shown is bounded to the allowlist's own precision.
    assert "87.15" in prompt
    assert "87.15032792538659" not in prompt
    assert "88.33" in prompt
    assert "0.25" in prompt

    # ...and every number it is shown can be quoted verbatim.
    for token in ("87.15", "87.2", "87", "88.33", "0.25"):
        assert token in allowlist, f"{token} missing from allowlist"

    # End to end: a field citing the payload value exactly survives the guard.
    assert (
        validate_text_field(
            "Your shoulder turn measured 87.15 deg against a 0.25 deg floor.",
            allowlist,
            68,
        )
        == []
    )


def test_band_edges_survive_payload_rounding_unchanged() -> None:
    """Every hand-picked rubric band edge must round-trip exactly.

    Rounding must not move a real decision boundary: if `ideal_max` 0.08 were
    rendered as 0.1, an above-ideal 0.09 would read as sitting on the edge of
    the ideal range.
    """
    for bands in RUBRIC_V1.values():
        for name, band in bands.items():
            for edge in (band.ideal_min, band.ideal_max):
                assert round_payload_numbers(edge) == edge, f"{name} edge {edge} moved"
