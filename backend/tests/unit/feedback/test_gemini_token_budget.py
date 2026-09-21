"""Regression tests for the Gemini output-token budget (thinking vs answer).

The original defect: ``max_output_tokens`` was 800 and no thinking budget was
set, so ``gemini-2.5-flash`` spent 764 tokens thinking and 22 on the answer,
finished as ``MAX_TOKENS`` and returned 101 characters of truncated JSON.
``parse_gemini_response`` returned None on every call and Stage 17 fell back to
the deterministic template -- indistinguishable, from the outside, from "the
model declined to answer".

These tests need no API key: the SDK response is mocked with the real response
object's shape (``candidates[0].finish_reason``, ``usage_metadata`` with the
SDK's own field names, truncated ``text``).
"""

from __future__ import annotations

import logging
import sys
import types as pytypes
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.feedback.gemini import (
    GENERATION_MAX_OUTPUT_TOKENS,
    GENERATION_THINKING_BUDGET,
    MAX_TOKENS_FINISH_REASON,
    GeminiTruncatedError,
    GoogleGenAIClient,
    TokenUsage,
    generate_feedback,
    generation_config,
    parse_gemini_response,
    read_finish_reason,
    read_token_usage,
)
from app.models.enums import FeedbackSource
from app.models.feedback import FeedbackInput
from tests.conftest import make_core

#: Verbatim shape of the truncated body seen on the live call: an opening brace
#: and a partial "summary" value, cut off mid-token at 101 characters.
TRUNCATED_BODY: str = (
    '{\n  "summary": "Your forehand topspin shows a solid contact height and a '
    'well-timed strike, but the'
)


class _FinishReason:
    """Stand-in for ``google.genai.types.FinishReason``: an enum with ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:  # pragma: no cover - defensive only
        return f"FinishReason.{self.name}"


@dataclass
class _UsageMetadata:
    prompt_token_count: int
    thoughts_token_count: int
    candidates_token_count: int
    total_token_count: int


@dataclass
class _Candidate:
    finish_reason: Any


@dataclass
class _Response:
    """Mirrors the SDK response surface the adapter actually touches."""

    text: str | None
    candidates: list[_Candidate]
    usage_metadata: _UsageMetadata


@dataclass
class _FakeModels:
    response: _Response
    configs: list[dict[str, Any]] = field(default_factory=list)

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> _Response:
        self.configs.append(config)
        return self.response


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, response: _Response) -> _FakeModels:
    """Make ``from google import genai`` resolve to a fake returning ``response``."""
    models = _FakeModels(response=response)
    genai_module = pytypes.ModuleType("google.genai")
    genai_module.Client = lambda api_key: pytypes.SimpleNamespace(  # type: ignore[attr-defined]
        models=models
    )
    google_module = pytypes.ModuleType("google")
    google_module.genai = genai_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    return models


def _truncated_response() -> _Response:
    return _Response(
        text=TRUNCATED_BODY,
        candidates=[_Candidate(finish_reason=_FinishReason(MAX_TOKENS_FINISH_REASON))],
        usage_metadata=_UsageMetadata(
            prompt_token_count=2431,
            thoughts_token_count=764,
            candidates_token_count=22,
            total_token_count=3217,
        ),
    )


# --------------------------------------------------------------------------- #
# The truncated body itself is unparseable -- this is the observed symptom.
# --------------------------------------------------------------------------- #


def test_truncated_body_is_unparseable() -> None:
    assert parse_gemini_response(TRUNCATED_BODY) is None


# --------------------------------------------------------------------------- #
# Truncation is detected, logged and raised as a distinct error.
# --------------------------------------------------------------------------- #


def test_max_tokens_finish_reason_raises_distinct_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch, _truncated_response())
    client = GoogleGenAIClient(api_key="test-key-not-a-real-key")
    with pytest.raises(GeminiTruncatedError) as excinfo:
        client.generate(
            system_instruction="sys", prompt="prompt", config=generation_config()
        )
    error = excinfo.value
    assert error.finish_reason == MAX_TOKENS_FINISH_REASON
    assert error.usage == TokenUsage(
        prompt_tokens=2431,
        thoughts_tokens=764,
        candidates_tokens=22,
        total_tokens=3217,
    )


def test_max_tokens_is_logged_with_the_token_split(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_fake_sdk(monkeypatch, _truncated_response())
    client = GoogleGenAIClient(api_key="test-key-not-a-real-key")
    with caplog.at_level(logging.WARNING, logger="tennisform.feedback"):
        with pytest.raises(GeminiTruncatedError):
            client.generate(
                system_instruction="sys", prompt="prompt", config=generation_config()
            )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "truncation must be observable in the logs"
    message = warnings[0].getMessage()
    assert "truncated" in message
    assert "thoughts=764" in message
    assert "candidates=22" in message


def test_complete_response_is_returned_and_not_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(
        text='{"summary": "ok", "strengths": [], "improvements": []}',
        candidates=[_Candidate(finish_reason=_FinishReason("STOP"))],
        usage_metadata=_UsageMetadata(
            prompt_token_count=2431,
            thoughts_token_count=0,
            candidates_token_count=540,
            total_token_count=2971,
        ),
    )
    models = _install_fake_sdk(monkeypatch, response)
    client = GoogleGenAIClient(api_key="test-key-not-a-real-key")
    raw = client.generate(
        system_instruction="sys", prompt="prompt", config=generation_config()
    )
    assert parse_gemini_response(raw) is not None
    # The thinking budget really is sent on the wire, not merely defined.
    assert models.configs[0]["thinking_config"] == {
        "thinking_budget": GENERATION_THINKING_BUDGET
    }


# --------------------------------------------------------------------------- #
# Truncation is distinguishable from every other fallback cause.
# --------------------------------------------------------------------------- #


class _TruncatingClient:
    def generate(
        self, *, system_instruction: str, prompt: str, config: dict[str, Any]
    ) -> str:
        raise GeminiTruncatedError(
            TokenUsage(
                prompt_tokens=2431,
                thoughts_tokens=764,
                candidates_tokens=22,
                total_tokens=3217,
            )
        )


class _FailingClient:
    def generate(
        self, *, system_instruction: str, prompt: str, config: dict[str, Any]
    ) -> str:
        raise RuntimeError("connection reset")


class _UnparseableClient:
    def generate(
        self, *, system_instruction: str, prompt: str, config: dict[str, Any]
    ) -> str:
        return "sorry, I can't do that"


def test_truncation_is_reported_in_the_guard_report() -> None:
    core: FeedbackInput = make_core(contact_confidence=0.9)
    feedback = generate_feedback(core, _TruncatingClient())
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.guard.model_finish_reason == MAX_TOKENS_FINISH_REASON


@pytest.mark.parametrize(
    "client", [_FailingClient(), _UnparseableClient()], ids=["transport", "unparseable"]
)
def test_other_failures_do_not_claim_truncation(client: Any) -> None:
    core: FeedbackInput = make_core(contact_confidence=0.9)
    feedback = generate_feedback(core, client)
    assert feedback.source is FeedbackSource.TEMPLATE
    assert feedback.guard.model_finish_reason is None


def test_skip_path_does_not_claim_truncation() -> None:
    core: FeedbackInput = make_core(contact_confidence=0.10)
    feedback = generate_feedback(core, _TruncatingClient())
    assert feedback.guard.model_finish_reason is None


# --------------------------------------------------------------------------- #
# Readers tolerate a response that carries no metadata at all.
# --------------------------------------------------------------------------- #


def test_readers_tolerate_a_bare_response() -> None:
    bare = pytypes.SimpleNamespace(text="{}")
    assert read_finish_reason(bare) is None
    assert read_token_usage(bare) == TokenUsage()


def test_budget_leaves_headroom_over_a_complete_response() -> None:
    # A complete draft measured at ~550 tokens; the ceiling must not be near it.
    assert GENERATION_MAX_OUTPUT_TOKENS >= 2048
    assert GENERATION_THINKING_BUDGET == 0
