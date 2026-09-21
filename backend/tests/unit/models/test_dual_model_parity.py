"""Structural parity between the duplicated ``feedback``/``responses`` models.

Two Pydantic classes share each of these names: a feedback-layer copy in
``app.models.feedback`` and a wire-layer copy in ``app.models.responses``. They
are projected into each other by hand (``to_wire_feedback`` in
``app/services/pipeline.py``, ``app/feedback/projection.py``). That projection's
docstring claims hand-writing it turns field drift into "a type error here
instead of a silent drop" -- but that only holds for REQUIRED fields. An
optional field with a default, added to one side and not the other, produces no
error anywhere.

That is exactly how ``NumericGuardReport.model_finish_reason`` was lost: added
to the ``feedback.py`` copy in 29283bd, missing from the ``responses.py`` copy,
silently dropping a truncation signal before persistence until 5d6861a. The
``MetricScore`` pair has the same dual-definition shape and the same risk.

These tests compare field-name sets so the divergence fails at the moment it is
introduced, rather than surfacing later as a live incident.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.models import feedback, responses


class ExpectedDivergence(BaseModel):
    """The field-name divergence a pair is allowed to have, pinned exactly.

    Empty sets mean strict parity. A non-empty set records an intentional,
    documented shape difference; pinning it keeps the test honest, because any
    NEW field on either side still fails.
    """

    only_in_feedback: frozenset[str] = frozenset()
    only_in_responses: frozenset[str] = frozenset()


#: One entry per class name defined in BOTH ``app.models.feedback`` and
#: ``app.models.responses``.
DUAL_MODEL_PAIRS: dict[str, ExpectedDivergence] = {
    # Must agree field for field: this pair is a rename-free copy, and the
    # model_finish_reason bug is what happens when it silently stops agreeing.
    "NumericGuardReport": ExpectedDivergence(),
    # Intentionally different shapes: the feedback copy is the narrower
    # Gemini-payload projection, the responses copy is the richer wire model
    # (it carries rubric weight and category). Pinned so that a genuinely new
    # field on either side is still caught.
    "MetricScore": ExpectedDivergence(
        only_in_feedback=frozenset({"score"}),
        only_in_responses=frozenset({"score_0_100", "weight", "category"}),
    ),
    # Strict parity, verified against the live field sets: both copies carry
    # summary/strengths/improvements/source/model/guard/latency_ms.
    # ``to_wire_feedback`` hand-projects all seven, and the dropped
    # ``model_finish_reason`` lived inside this very construction -- so this
    # pair is the closest neighbour to the original bug.
    "CoachingFeedback": ExpectedDivergence(),
    # Strict parity: priority/title/why/cue/drill/metric_refs on both copies,
    # all six rebuilt field for field in the ``to_wire_feedback`` comprehension.
    "Improvement": ExpectedDivergence(),
    # Strict parity on the same four fields
    # (ball_speed_mph/confidence/detections_used/unavailable_reason). The
    # projection runs the other way here -- ``to_feedback_ball_speed`` in
    # ``app/feedback/projection.py`` -- and threads all four.
    "BallSpeedResult": ExpectedDivergence(),
}


def _describe(
    class_name: str,
    unexpected_in_feedback: set[str],
    unexpected_in_responses: set[str],
    expected: ExpectedDivergence,
) -> str:
    return (
        f"{class_name} field sets drifted between "
        f"app.models.feedback.{class_name} and app.models.responses.{class_name}. "
        f"A field was added to one copy and not the other; because it has a "
        f"default, nothing else will fail.\n"
        f"  unexpectedly only in feedback.py: {sorted(unexpected_in_feedback)}\n"
        f"  unexpectedly only in responses.py: {sorted(unexpected_in_responses)}\n"
        f"  (known, intentional divergence for this pair -- "
        f"only in feedback.py: {sorted(expected.only_in_feedback)}, "
        f"only in responses.py: {sorted(expected.only_in_responses)})\n"
        f"Fix: add the field to both copies and thread it through the hand-written "
        f"projection, or update DUAL_MODEL_PAIRS if the divergence is deliberate."
    )


@pytest.mark.parametrize("class_name", sorted(DUAL_MODEL_PAIRS))
def test_dual_defined_models_have_matching_field_sets(class_name: str) -> None:
    expected = DUAL_MODEL_PAIRS[class_name]

    feedback_fields = set(getattr(feedback, class_name).model_fields)
    responses_fields = set(getattr(responses, class_name).model_fields)

    unexpected_in_feedback = (feedback_fields - responses_fields) - set(
        expected.only_in_feedback
    )
    unexpected_in_responses = (responses_fields - feedback_fields) - set(
        expected.only_in_responses
    )

    assert not unexpected_in_feedback and not unexpected_in_responses, _describe(
        class_name, unexpected_in_feedback, unexpected_in_responses, expected
    )


@pytest.mark.parametrize("class_name", sorted(DUAL_MODEL_PAIRS))
def test_pinned_divergence_is_still_real(class_name: str) -> None:
    """A pinned exception that no longer exists must be deleted, not left to rot."""
    expected = DUAL_MODEL_PAIRS[class_name]

    feedback_fields = set(getattr(feedback, class_name).model_fields)
    responses_fields = set(getattr(responses, class_name).model_fields)

    stale_feedback = set(expected.only_in_feedback) - (feedback_fields - responses_fields)
    stale_responses = set(expected.only_in_responses) - (responses_fields - feedback_fields)

    assert not stale_feedback and not stale_responses, (
        f"{class_name}: DUAL_MODEL_PAIRS pins a divergence that no longer exists. "
        f"Stale 'only_in_feedback' entries: {sorted(stale_feedback)}; "
        f"stale 'only_in_responses' entries: {sorted(stale_responses)}. "
        f"Remove them so the pair is checked strictly again."
    )
