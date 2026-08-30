"""Gemini payload construction, numeric guard, and the model call.

Layout of this module:

* PURE section -- payload building, prompt rendering, response parsing and the
  numeric guard. No I/O, no network, no clock. Fully unit-testable.
* IMPURE section -- ``GeminiClient`` (a protocol), ``GoogleGenAIClient`` (the
  real SDK adapter, imported lazily) and ``generate_feedback`` (orchestration).

Gemini's only job here is turning already-computed numbers into readable text.
It never measures and never classifies: the payload contains numbers, enum
strings and verdicts only -- no video, frames, landmarks, raw keypoints, user
email, user id, or calibration tap coordinates.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import ValidationError

from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    FeedbackSource,
)
from app.models.feedback import (
    CoachingFeedback,
    FeedbackInput,
    FeedbackPayload,
    GeminiFeedbackDraft,
    NumericGuardReport,
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MODEL_NAME: Final[str] = "gemini-2.5-flash"

#: Environment variable holding the API key. Never hardcode a key.
GEMINI_API_KEY_ENV: Final[str] = "GEMINI_API_KEY"

REQUEST_TIMEOUT_S: Final[float] = 8.0

#: Below this contact confidence the swing measurements are not trustworthy
#: enough to be narrated, so Gemini is skipped entirely (no network call).
MIN_CONTACT_CONFIDENCE_FOR_GEMINI: Final[float] = 0.45

#: Fixed message returned when the Gemini call is skipped for low contact
#: confidence or a truncated clip.
LOW_CONFIDENCE_FALLBACK_MESSAGE: Final[str] = (
    "This clip could not be analysed reliably. The contact point was unclear or "
    "the swing ran past the edge of the recording, so no coaching feedback was "
    "generated. Re-record with the whole swing in frame, filmed from the side, "
    "and try again."
)

#: Fixed message used when the model output cannot be trusted or parsed.
GUARD_FALLBACK_MESSAGE: Final[str] = (
    "Coaching text could not be generated for this clip. Your measured swing "
    "numbers and scores above are unaffected and remain accurate."
)

MAX_MPH_MENTIONS: Final[int] = 2

#: Unconditionally banned unit substrings (PIPELINE.md Stage 17 step 4).
BANNED_UNIT_SUBSTRINGS: Final[frozenset[str]] = frozenset(
    {
        "km/h",
        "m/s",
        "kph",
        "rpm",
        "rally",
        "spin rate",
        "revolutions",
        "meters",
        "metres",
        "feet",
        "inches",
    }
)

#: Banned iff ``ball_speed_mph`` is None; otherwise governed by the binding rule.
CONDITIONAL_MPH_SUBSTRING: Final[str] = "mph"

NUMERIC_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[-+]?\d+(?:\.\d+)?%?")
MPH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"(?i)\bmph\b")
BOUND_MPH_RE_TEMPLATE: Final[str] = r"(?i)\b{value}\s?mph\b"

#: List positions the model may legitimately write.
ORDINAL_ALLOWLIST: Final[frozenset[str]] = frozenset({"1", "2", "3", "4", "5"})

BALL_SPEED_DEFINITION: Final[str] = (
    "average speed over the first 0.25 seconds after contact, not the "
    "instantaneous speed off the racket"
)

BALL_SPEED_UNAVAILABLE_DEFINITION: Final[str] = (
    "Ball speed was not measured for this clip."
)

BALL_SPEED_UNAVAILABLE_REPORTING_RULE: Final[str] = (
    "Do not state, estimate, imply, or hint at any ball speed. Do not use the "
    "word 'mph' or any speed unit. If the user would expect a speed, say only "
    "that speed was not measured for this clip."
)

#: Hedge applied by Python, never chosen by the model (PIPELINE.md Stage 16).
HEDGE_BY_CONFIDENCE: Final[dict[str, str]] = {
    BallSpeedConfidence.HIGH.value: "measured",
    BallSpeedConfidence.MEDIUM.value: "approximate",
    BallSpeedConfidence.LOW.value: "rough",
    BallSpeedConfidence.UNAVAILABLE.value: "none",
}

UNIT_GLOSSARY: Final[dict[str, str]] = {
    "TU": "torso units; 1 TU = one shoulder-to-hip torso length of this player",
    "deg": "degrees",
    "ratio": "dimensionless; 0 = hip height, 1 = shoulder height",
    "mph": (
        "miles per hour; the ONLY real-world unit in this payload, measured from "
        "ball detection plus a user-supplied court calibration distance"
    ),
}

SYSTEM_INSTRUCTION: Final[str] = (
    "You are a text formatter for a tennis coaching app. You will receive "
    "measurements that have already been computed by a deterministic analysis "
    "system. You must NEVER compute, estimate, infer, convert, or invent any "
    "number. Every numeric value in your output must appear verbatim in the "
    "input JSON. Never convert torso units to feet, meters, inches, or miles per "
    "hour. Never state a spin rate or a rally count - this system does not "
    "measure those. Ball speed: if `ball_speed.mph` is a number, you may state "
    'it at most twice, exactly as that integer immediately followed by the word '
    '"mph", and you must respect the `hedge` field. If `ball_speed.mph` is null, '
    "you must not mention ball speed, mph, km/h, or any speed at all. Never "
    "derive ball speed from hand speed or from any other value. Do not "
    "contradict the provided shot_type. Do not add measurements that are not in "
    "the input. If a metric is null, say the measurement was unavailable for "
    "this clip."
)

GENERATION_TEMPERATURE: Final[float] = 0.3
GENERATION_MAX_OUTPUT_TOKENS: Final[int] = 800
GENERATION_RESPONSE_MIME_TYPE: Final[str] = "application/json"


# --------------------------------------------------------------------------- #
# PURE -- payload construction
# --------------------------------------------------------------------------- #


def hedge_for(confidence: str) -> str:
    """Map a ball-speed confidence tier onto its hedge word."""
    return HEDGE_BY_CONFIDENCE.get(str(confidence), "none")


def _reporting_rule_for(value: int) -> str:
    return (
        f"State this value at most twice, only as the exact integer {value} "
        "immediately followed by 'mph'. Never round it, never alter it, never "
        "convert it, never state a range around it, never compare it to a target "
        "or ideal value. It is a measurement, not a score."
    )


def ball_speed_block(result: Any) -> dict[str, Any]:
    """Build the ball-speed sub-payload.

    The block is ALWAYS present. When the speed is unavailable it is neutered
    rather than omitted, because an absent key invites the model to fill the gap.
    """
    mph = result.ball_speed_mph
    if mph is None:
        reason = result.unavailable_reason or BallSpeedUnavailableReason.NOT_CALIBRATED
        return {
            "mph": None,
            "confidence": BallSpeedConfidence.UNAVAILABLE.value,
            "reason": str(reason),
            "definition": BALL_SPEED_UNAVAILABLE_DEFINITION,
            "reporting_rule": BALL_SPEED_UNAVAILABLE_REPORTING_RULE,
        }
    return {
        "mph": int(mph),
        "confidence": str(result.confidence),
        "detections_used": int(result.detections_used),
        "definition": BALL_SPEED_DEFINITION,
        "hedge": hedge_for(result.confidence),
        "reporting_rule": _reporting_rule_for(int(mph)),
    }


def build_feedback_payload(core: FeedbackInput) -> FeedbackPayload:
    """Render the text-only Gemini payload (PIPELINE.md Stage 16)."""
    metrics: list[dict[str, Any]] = [
        {
            "name": m.name,
            "value": m.value,
            "unit": str(m.unit),
            "verdict": str(m.verdict),
            "score": m.score,
            "ideal_min": m.ideal_min,
            "ideal_max": m.ideal_max,
            "view_sensitive": m.view_sensitive,
        }
        for m in core.metrics
    ]
    return {
        "pipeline_version": core.pipeline_version,
        "rubric_version": core.rubric_version,
        "shot_type": core.shot_type,
        "shot_type_confidence": core.shot_type_confidence,
        "shot_type_evidence": list(core.shot_type_evidence),
        "handedness": core.handedness,
        "contact_confidence": core.contact_confidence,
        "overall_score": core.overall_score,
        "category_scores": dict(core.category_scores),
        "metrics": metrics,
        "ball_speed": ball_speed_block(core.ball_speed),
        "priority_metric_names": list(core.priority_metric_names),
        "unit_glossary": dict(UNIT_GLOSSARY),
        "unavailable_metrics": [m.name for m in core.metrics if m.value is None],
        "low_confidence_warnings": list(core.low_confidence_warnings),
    }


def _add_number_renderings(value: float, out: set[str], integer_only: bool) -> None:
    if integer_only:
        out.add(str(int(value)))
        return
    if isinstance(value, int):
        out.add(str(value))
        return
    out.add(f"{value:.0f}")
    out.add(f"{value:.1f}")


def _walk_numbers(node: Any, out: set[str], integer_only: bool) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        _add_number_renderings(node, out, integer_only)
        return
    if isinstance(node, Mapping):
        for key, child in node.items():
            _walk_numbers(child, out, integer_only or key == "ball_speed")
        return
    if isinstance(node, (list, tuple)):
        for child in node:
            _walk_numbers(child, out, integer_only)


def numeric_allowlist(payload: FeedbackPayload) -> frozenset[str]:
    """Every numeric string rendering the model is permitted to emit.

    Floats are allowlisted at 0 and 1 decimal places. Everything under
    ``ball_speed`` (``mph``, ``detections_used``) is allowlisted as an INTEGER
    rendering only -- so ``"68.0 mph"`` is a violation by construction.
    """
    out: set[str] = set()
    _walk_numbers(payload, out, integer_only=False)
    out.update(ORDINAL_ALLOWLIST)
    return frozenset(out)


def render_user_prompt(payload: FeedbackPayload) -> str:
    """Render the text-only user prompt. Numbers, enum strings and verdicts only."""
    body = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)
    return (
        "Write coaching feedback from the measurements below. Every number you "
        "write must appear verbatim in this JSON. Coach only the metrics named "
        "in `priority_metric_names`. Obey `ball_speed.reporting_rule` exactly.\n\n"
        f"{body}"
    )


def response_schema() -> dict[str, Any]:
    """Explicit response schema matching ``GeminiFeedbackDraft``."""
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "improvements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "priority": {"type": "integer"},
                        "title": {"type": "string"},
                        "why": {"type": "string"},
                        "cue": {"type": "string"},
                        "drill": {"type": "string"},
                        "metric_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "priority",
                        "title",
                        "why",
                        "cue",
                        "drill",
                        "metric_refs",
                    ],
                },
            },
        },
        "required": ["summary", "strengths", "improvements"],
    }


def generation_config() -> dict[str, Any]:
    """Generation config for the Gemini call (PIPELINE.md Stage 16)."""
    return {
        "temperature": GENERATION_TEMPERATURE,
        "max_output_tokens": GENERATION_MAX_OUTPUT_TOKENS,
        "response_mime_type": GENERATION_RESPONSE_MIME_TYPE,
        "response_schema": response_schema(),
    }


# --------------------------------------------------------------------------- #
# PURE -- numeric guard
# --------------------------------------------------------------------------- #


def _normalize(text: str) -> str:
    """Normalize unicode separators so the binding rule cannot be evaded."""
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.replace(" ", " ").replace(" ", " ")


def extract_numeric_tokens(text: str) -> list[str]:
    """Every numeric token in ``text``."""
    return NUMERIC_TOKEN_RE.findall(_normalize(text))


def find_banned_units(text: str) -> list[str]:
    """Unconditionally banned unit substrings present in ``text``."""
    lowered = _normalize(text).lower()
    return sorted(unit for unit in BANNED_UNIT_SUBSTRINGS if unit in lowered)


def check_mph_binding(
    text: str,
    ball_speed_mph: int | None,
    max_mentions: int = MAX_MPH_MENTIONS,
) -> list[str]:
    """Enforce the conditional ``mph`` rule (PIPELINE.md Stage 17 step 5).

    * ``ball_speed_mph is None`` -> ANY occurrence of ``mph`` is a violation.
    * ``ball_speed_mph == V``    -> the count of ``mph`` occurrences must equal
      the count of ``\\bV\\s?mph\\b`` matches, and must be <= ``max_mentions``.
    """
    normalized = _normalize(text)
    occurrences = MPH_TOKEN_RE.findall(normalized)
    if not occurrences:
        return []
    if ball_speed_mph is None:
        return [CONDITIONAL_MPH_SUBSTRING]

    bound_re = re.compile(BOUND_MPH_RE_TEMPLATE.format(value=int(ball_speed_mph)))
    bound_count = len(bound_re.findall(normalized))
    violations: list[str] = []
    if bound_count != len(occurrences):
        violations.append(CONDITIONAL_MPH_SUBSTRING)
    if len(occurrences) > max_mentions:
        violations.append(f"{CONDITIONAL_MPH_SUBSTRING}:too_many")
    return violations


def validate_text_field(
    text: str,
    allowlist: frozenset[str],
    ball_speed_mph: int | None,
) -> list[str]:
    """Return every violation found in one model-authored text field."""
    violations: list[str] = []
    for token in extract_numeric_tokens(text):
        if token not in allowlist:
            violations.append(token)
    violations.extend(find_banned_units(text))
    violations.extend(check_mph_binding(text, ball_speed_mph))
    return violations


def _mph_rule(ball_speed_mph: int | None) -> str:
    return "banned" if ball_speed_mph is None else f"bound_to:{int(ball_speed_mph)}"


def enforce(
    draft: GeminiFeedbackDraft,
    allowlist: frozenset[str],
    ball_speed_mph: int | None,
) -> tuple[GeminiFeedbackDraft | None, NumericGuardReport]:
    """Discard every field containing a violation. Never rewrites, never raises.

    Returns ``(None, report)`` when the summary is discarded or more than two
    fields are discarded -- the caller then falls back to deterministic text.
    """
    rejected: list[str] = []
    discarded: list[str] = []

    summary_violations = validate_text_field(draft.summary, allowlist, ball_speed_mph)
    if summary_violations:
        rejected.extend(summary_violations)
        discarded.append("summary")

    kept_strengths: list[str] = []
    for index, strength in enumerate(draft.strengths):
        found = validate_text_field(strength, allowlist, ball_speed_mph)
        if found:
            rejected.extend(found)
            discarded.append(f"strengths[{index}]")
        else:
            kept_strengths.append(strength)

    kept_improvements = []
    for index, improvement in enumerate(draft.improvements):
        improvement_violations: list[str] = []
        for field_name in ("title", "why", "cue", "drill"):
            improvement_violations.extend(
                validate_text_field(
                    getattr(improvement, field_name), allowlist, ball_speed_mph
                )
            )
        if improvement_violations:
            rejected.extend(improvement_violations)
            discarded.append(f"improvements[{index}]")
        else:
            kept_improvements.append(improvement)

    fell_back = "summary" in discarded or len(discarded) > 2
    report = NumericGuardReport(
        passed=not discarded,
        rejected_tokens=rejected,
        fields_discarded=discarded,
        fell_back_to_template=fell_back,
        mph_rule=_mph_rule(ball_speed_mph),
    )
    if fell_back:
        return None, report

    cleaned = GeminiFeedbackDraft(
        summary=draft.summary,
        strengths=kept_strengths,
        improvements=kept_improvements,
    )
    return cleaned, report


# --------------------------------------------------------------------------- #
# PURE -- response parsing
# --------------------------------------------------------------------------- #

_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL
)


def strip_code_fences(raw: str) -> str:
    """Return the JSON body of ``raw``, unwrapping ``` / ```json fences if present."""
    match = _FENCE_RE.search(raw)
    if match is not None:
        return match.group(1).strip()
    return raw.strip()


def parse_gemini_response(raw: str) -> GeminiFeedbackDraft | None:
    """Parse a model response into a draft. Returns ``None`` on malformed output.

    Handles bare JSON and JSON wrapped in markdown fences. Never raises.
    """
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(strip_code_fences(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return GeminiFeedbackDraft.model_validate(data)
    except ValidationError:
        return None


# --------------------------------------------------------------------------- #
# PURE -- skip decision and fallback
# --------------------------------------------------------------------------- #


def should_skip_gemini(core: FeedbackInput) -> bool:
    """True when the clip is too unreliable to narrate, so Gemini is not called."""
    return (
        core.truncated_clip
        or core.contact_confidence < MIN_CONTACT_CONFIDENCE_FOR_GEMINI
    )


def _fallback_feedback(message: str, guard: NumericGuardReport) -> CoachingFeedback:
    return CoachingFeedback(
        summary=message,
        strengths=[],
        improvements=[],
        source=FeedbackSource.TEMPLATE,
        model=None,
        guard=guard,
        latency_ms=None,
    )


def skipped_feedback(core: FeedbackInput) -> CoachingFeedback:
    """Deterministic feedback for the low-confidence / truncated-clip skip path."""
    return _fallback_feedback(
        LOW_CONFIDENCE_FALLBACK_MESSAGE,
        NumericGuardReport(
            passed=True,
            fell_back_to_template=True,
            mph_rule=_mph_rule(core.ball_speed.ball_speed_mph),
        ),
    )


# --------------------------------------------------------------------------- #
# IMPURE -- client and orchestration
# --------------------------------------------------------------------------- #


@runtime_checkable
class GeminiClient(Protocol):
    """Thin seam over the model call so tests need no SDK and no network."""

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        config: dict[str, Any],
    ) -> str:
        """Return the raw text of the model response."""
        ...


class GoogleGenAIClient:
    """Adapter over the Google GenAI SDK. The SDK is imported lazily."""

    def __init__(self, api_key: str | None = None, model: str = MODEL_NAME) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(
            GEMINI_API_KEY_ENV
        )
        self._model = model

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        config: dict[str, Any],
    ) -> str:
        if not self._api_key:
            raise RuntimeError(
                f"{GEMINI_API_KEY_ENV} is not set; cannot call the Gemini API."
            )
        from google import genai  # type: ignore[import-not-found]

        client = genai.Client(api_key=self._api_key)
        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
            config={"system_instruction": system_instruction, **config},
        )
        return response.text or ""


def generate_feedback(
    core: FeedbackInput,
    client: GeminiClient,
) -> CoachingFeedback:
    """Turn computed numbers into coaching text. Never raises out to the caller.

    Skips the model entirely -- no network call at all -- when contact confidence
    is below :data:`MIN_CONTACT_CONFIDENCE_FOR_GEMINI` or the clip was truncated.
    """
    if should_skip_gemini(core):
        return skipped_feedback(core)

    ball_speed_mph = core.ball_speed.ball_speed_mph
    payload = build_feedback_payload(core)
    allowlist = numeric_allowlist(payload)
    guard_default = NumericGuardReport(
        passed=False,
        fell_back_to_template=True,
        mph_rule=_mph_rule(ball_speed_mph),
    )

    started = time.perf_counter()
    try:
        raw = client.generate(
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=render_user_prompt(payload),
            config=generation_config(),
        )
    except Exception:  # noqa: BLE001 - the API never fails because Gemini failed
        return _fallback_feedback(GUARD_FALLBACK_MESSAGE, guard_default)
    latency_ms = int((time.perf_counter() - started) * 1000)

    draft = parse_gemini_response(raw)
    if draft is None:
        return _fallback_feedback(GUARD_FALLBACK_MESSAGE, guard_default)

    cleaned, report = enforce(draft, allowlist, ball_speed_mph)
    if cleaned is None:
        return _fallback_feedback(GUARD_FALLBACK_MESSAGE, report)

    source = FeedbackSource.GEMINI if report.passed else FeedbackSource.GEMINI_PARTIAL
    return CoachingFeedback(
        summary=cleaned.summary,
        strengths=cleaned.strengths,
        improvements=cleaned.improvements,
        source=source,
        model=MODEL_NAME,
        guard=report,
        latency_ms=latency_ms,
    )


__all__: Sequence[str] = (
    "BANNED_UNIT_SUBSTRINGS",
    "CONDITIONAL_MPH_SUBSTRING",
    "GEMINI_API_KEY_ENV",
    "GUARD_FALLBACK_MESSAGE",
    "HEDGE_BY_CONFIDENCE",
    "LOW_CONFIDENCE_FALLBACK_MESSAGE",
    "MAX_MPH_MENTIONS",
    "MIN_CONTACT_CONFIDENCE_FOR_GEMINI",
    "MODEL_NAME",
    "SYSTEM_INSTRUCTION",
    "GeminiClient",
    "GoogleGenAIClient",
    "ball_speed_block",
    "build_feedback_payload",
    "check_mph_binding",
    "enforce",
    "extract_numeric_tokens",
    "find_banned_units",
    "generate_feedback",
    "generation_config",
    "hedge_for",
    "numeric_allowlist",
    "parse_gemini_response",
    "render_user_prompt",
    "response_schema",
    "should_skip_gemini",
    "skipped_feedback",
    "strip_code_fences",
    "validate_text_field",
)
