"""The conditional `mph` rule, in isolation (PIPELINE.md Stage 17 step 5)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.feedback.gemini import (
    GUARD_FALLBACK_MESSAGE,
    check_mph_binding,
    enforce,
    generate_feedback,
    numeric_allowlist,
    build_feedback_payload,
    validate_text_field,
)
from app.models.enums import FeedbackSource
from app.models.feedback import FeedbackInput, GeminiFeedbackDraft
from tests.conftest import FakeGeminiClient

# --------------------------------------------------------------------------- #
# ball_speed_mph is None -> 'mph' is a banned substring
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "Ball speed was 68 mph.",
        "Roughly 70 mph off the strings.",
        "mph",
        "Speed in MPH was solid.",
        "It read 68 mph.",
    ],
)
def test_any_mph_is_a_violation_when_speed_is_null(text: str) -> None:
    assert check_mph_binding(text, None) == ["mph"]


def test_no_mph_is_clean_when_speed_is_null() -> None:
    assert check_mph_binding("Your contact point was solid.", None) == []


# --------------------------------------------------------------------------- #
# ball_speed_mph == V -> binding rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "Ball speed came out around 68 mph.",
        "68 mph is a good strike.",
        "It was 68 mph, and 68 mph is respectable.",
        "It read 68 mph on the clip.",
        "It read 68 MPH on the clip.",
    ],
)
def test_correct_binding_is_accepted(text: str) -> None:
    assert check_mph_binding(text, 68) == []


@pytest.mark.parametrize(
    "text",
    [
        "Ball speed was 68.0 mph.",
        "You hit about 70 mph.",
        "Speed is measured in mph.",
        "68 mph, then 68 mph, then 68 mph again.",
        "That is 168 mph.",
        "It was 68 - mph.",
        "6 8 mph.",
    ],
)
def test_incorrect_binding_is_rejected(text: str) -> None:
    assert check_mph_binding(text, 68) != []


def test_more_than_two_mentions_is_rejected_even_when_bound() -> None:
    text = "68 mph. 68 mph. 68 mph."
    violations = check_mph_binding(text, 68)
    assert "mph:too_many" in violations


def test_two_mentions_is_the_limit() -> None:
    assert check_mph_binding("68 mph and 68 mph", 68) == []


def test_max_mentions_is_configurable() -> None:
    assert check_mph_binding("68 mph and 68 mph", 68, max_mentions=1) != []


# --------------------------------------------------------------------------- #
# Interaction with the numeric allowlist
# --------------------------------------------------------------------------- #


def test_68_point_0_is_also_a_numeric_violation(
    core_with_speed: FeedbackInput,
) -> None:
    allowlist = numeric_allowlist(build_feedback_payload(core_with_speed))
    violations = validate_text_field("You hit 68.0 mph.", allowlist, 68)
    assert "68.0" in violations
    assert "mph" in violations


def test_bare_integer_is_allowlisted_but_binding_is_evaluated_independently(
    core_with_speed: FeedbackInput,
) -> None:
    allowlist = numeric_allowlist(build_feedback_payload(core_with_speed))
    # The integer alone is fine.
    assert validate_text_field("The reading was 68.", allowlist, 68) == []
    # Detaching the unit from the number is not.
    assert validate_text_field("The reading was 68, in mph.", allowlist, 68) == ["mph"]


# --------------------------------------------------------------------------- #
# End-to-end through enforce() and generate_feedback()
# --------------------------------------------------------------------------- #


def _draft(summary: str) -> GeminiFeedbackDraft:
    return GeminiFeedbackDraft(summary=summary, strengths=[], improvements=[])


def test_enforce_reports_mph_rule_banned_when_speed_is_null(
    core_without_speed: FeedbackInput,
) -> None:
    allowlist = numeric_allowlist(build_feedback_payload(core_without_speed))
    cleaned, report = enforce(_draft("Nice, compact swing."), allowlist, None)
    assert cleaned is not None
    assert report.passed is True
    assert report.mph_rule == "banned"


def test_enforce_reports_mph_rule_bound_when_speed_is_present(
    core_with_speed: FeedbackInput,
) -> None:
    allowlist = numeric_allowlist(build_feedback_payload(core_with_speed))
    cleaned, report = enforce(_draft("Struck at 68 mph."), allowlist, 68)
    assert cleaned is not None
    assert report.mph_rule == "bound_to:68"


def _fenced(summary: str) -> str:
    payload: dict[str, Any] = {
        "summary": summary,
        "strengths": [],
        "improvements": [],
    }
    return json.dumps(payload)


def test_null_speed_response_mentioning_mph_falls_back(
    core_without_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=_fenced("You struck it at 68 mph."))
    feedback = generate_feedback(core_without_speed, client)
    assert client.call_count == 1
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.summary == GUARD_FALLBACK_MESSAGE
    assert feedback.guard.mph_rule == "banned"
    assert "mph" in feedback.guard.rejected_tokens


def test_present_speed_with_decimal_rendering_falls_back(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=_fenced("You struck it at 68.0 mph."))
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.guard.mph_rule == "bound_to:68"
    assert "68.0" in feedback.guard.rejected_tokens


def test_present_speed_with_correct_binding_is_accepted(
    core_with_speed: FeedbackInput,
) -> None:
    client = FakeGeminiClient(response=_fenced("You struck it at 68 mph."))
    feedback = generate_feedback(core_with_speed, client)
    assert feedback.source is FeedbackSource.GEMINI
    assert feedback.summary == "You struck it at 68 mph."
    assert feedback.guard.passed is True
