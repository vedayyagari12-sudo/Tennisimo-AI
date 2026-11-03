"""The Gemini payload for a user-declared vs an inferred shot type.

Offline: the Gemini client is ``FakeGeminiClient``. Covers the payload content,
the evidence relabelling, the numeric guard with a null confidence, and that
the contact gates are untouched by a declaration.
"""

from __future__ import annotations

import json

import pytest

from app.analysis.scoring import build_scorecard
from app.analysis.shot_type import RULE_VERSION, resolve_shot_type
from app.feedback.gemini import (
    DECLARED_SHOT_TYPE_INSTRUCTION,
    MIN_CONTACT_CONFIDENCE_FOR_GEMINI,
    SYSTEM_INSTRUCTION,
    build_feedback_payload,
    enforce,
    generate_feedback,
    numeric_allowlist,
    should_skip_gemini,
)
from app.feedback.projection import build_feedback_input
from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    FeedbackSource,
    ShotType,
)
from app.models.feedback import FeedbackInput, GeminiFeedbackDraft
from app.models.responses import BallSpeedResult, ShotTypeInference, SwingMetrics
from app.services import pipeline
from tests.conftest import FakeGeminiClient, make_core

#: A classifier line that argues for a DIFFERENT type than the declaration.
SERVE_EVIDENCE: str = "contact height ratio 1.62 > 1.30 threshold -> serve"

CLEAN_DRAFT: str = json.dumps(
    {
        "summary": "A solid forehand shape with a 71.5 overall score.",
        "strengths": ["Contact height sat in the ideal band."],
        "improvements": [],
    }
)


def declared_core(contact_confidence: float = 0.74) -> FeedbackInput:
    """``make_core`` with the shot type declared by the user."""
    return make_core(contact_confidence=contact_confidence).model_copy(
        update={
            "shot_type": "forehand_topspin",
            "shot_type_confidence": None,
            "shot_type_source": "user_declared",
            "inferred_shot_type": "serve",
            "shot_type_evidence": [SERVE_EVIDENCE],
        }
    )


def inferred_core(contact_confidence: float = 0.74) -> FeedbackInput:
    return make_core(contact_confidence=contact_confidence).model_copy(
        update={"shot_type_evidence": [SERVE_EVIDENCE]}
    )


def test_inferred_payload_is_marked_inferred_and_keeps_its_evidence() -> None:
    payload = build_feedback_payload(inferred_core())

    assert payload["shot_type"] == "forehand_topspin"
    assert payload["shot_type_source"] == "inferred"
    assert payload["shot_type_confidence"] == 0.81
    assert payload["inferred_shot_type"] is None
    assert payload["shot_type_evidence"] == [SERVE_EVIDENCE]
    assert payload["classifier_cross_check_evidence"] == []


def test_declared_payload_is_marked_user_declared_with_null_confidence() -> None:
    payload = build_feedback_payload(declared_core())

    assert payload["shot_type"] == "forehand_topspin"
    assert payload["shot_type_source"] == "user_declared"
    assert payload["shot_type_confidence"] is None
    assert payload["inferred_shot_type"] == "serve"


def test_declared_payload_never_presents_other_type_evidence_as_the_rationale() -> None:
    payload = build_feedback_payload(declared_core())

    # The serve line is NOT under shot_type_evidence for a declared forehand...
    assert payload["shot_type_evidence"] == []
    # ...it is labelled as the classifier cross-check instead.
    assert payload["classifier_cross_check_evidence"] == [SERVE_EVIDENCE]


def test_system_prompt_carries_the_declared_sentence_once_and_keeps_the_old_rule() -> None:
    assert SYSTEM_INSTRUCTION.count(DECLARED_SHOT_TYPE_INSTRUCTION) == 1
    assert "Do not contradict the provided shot_type." in SYSTEM_INSTRUCTION
    assert "never assert that reading as fact" in DECLARED_SHOT_TYPE_INSTRUCTION


def test_numeric_guard_setup_is_unchanged_for_a_declared_null_confidence_payload() -> None:
    declared = declared_core()
    # The same core, but presented as inferred with the same null confidence:
    # relabelling the evidence must not add or remove a single allowed number.
    as_inferred = declared.model_copy(
        update={"shot_type_source": "inferred", "inferred_shot_type": None}
    )
    allowlist = numeric_allowlist(build_feedback_payload(declared))

    assert allowlist == numeric_allowlist(build_feedback_payload(as_inferred))
    # A null confidence contributes nothing and breaks nothing; the numbers that
    # ARE in the payload (overall_score) are still quotable.
    assert "71.5" in allowlist

    draft = GeminiFeedbackDraft.model_validate_json(CLEAN_DRAFT)
    cleaned, report = enforce(draft, allowlist, declared.ball_speed.ball_speed_mph)
    assert cleaned is not None
    assert report.passed is True


def test_generate_feedback_succeeds_for_a_declared_shot() -> None:
    client = FakeGeminiClient(response=CLEAN_DRAFT)
    feedback = generate_feedback(declared_core(), client)

    assert client.call_count == 1
    assert feedback.source is FeedbackSource.GEMINI
    assert feedback.guard.passed is True
    prompt = client.calls[0]["prompt"]
    assert '"shot_type_source": "user_declared"' in prompt
    assert '"shot_type_confidence": null' in prompt


def test_contact_gates_are_unchanged() -> None:
    assert MIN_CONTACT_CONFIDENCE_FOR_GEMINI == 0.45
    assert pipeline.MIN_CONTACT_CONFIDENCE == 0.35


@pytest.mark.parametrize(
    "contact",
    [0.0, 0.34, 0.35, 0.44, MIN_CONTACT_CONFIDENCE_FOR_GEMINI, 0.46, 1.0],
)
def test_should_skip_gemini_ignores_the_declaration(contact: float) -> None:
    expected = contact < MIN_CONTACT_CONFIDENCE_FOR_GEMINI
    assert should_skip_gemini(inferred_core(contact)) is expected
    assert should_skip_gemini(declared_core(contact)) is expected


def test_a_declared_shot_below_the_gate_still_skips_the_model() -> None:
    client = FakeGeminiClient(response=CLEAN_DRAFT)
    feedback = generate_feedback(declared_core(contact_confidence=0.44), client)

    assert client.call_count == 0
    assert feedback.source is FeedbackSource.TEMPLATE


def test_projection_threads_source_inferred_type_and_null_confidence() -> None:
    classifier = ShotTypeInference(
        shot_type=ShotType.SERVE,
        confidence=0.62,
        class_scores={"serve": 0.62, "volley": 0.38},
        evidence=[SERVE_EVIDENCE],
        rule_version=RULE_VERSION,
    )
    ball = BallSpeedResult(
        ball_speed_mph=None,
        confidence=BallSpeedConfidence.UNAVAILABLE,
        detections_used=0,
        unavailable_reason=BallSpeedUnavailableReason.NOT_CALIBRATED,
    )
    for declared, source, inferred, confidence in (
        (ShotType.FOREHAND_TOPSPIN, "user_declared", "serve", None),
        (None, "inferred", None, 0.62),
    ):
        shot_type = resolve_shot_type(
            declared, classifier, 0.8, min_contact_confidence=pipeline.MIN_CONTACT_CONFIDENCE
        )
        core = build_feedback_input(
            scorecard=build_scorecard(SwingMetrics(), shot_type.shot_type),
            shot_type=shot_type,
            handedness="right",
            contact_confidence=0.8,
            ball_speed=ball,
        )
        assert core.shot_type == shot_type.shot_type.value
        assert core.shot_type_source == source
        assert core.inferred_shot_type == inferred
        assert core.shot_type_confidence == confidence
        assert core.shot_type_evidence == [SERVE_EVIDENCE]
