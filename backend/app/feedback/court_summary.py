"""Court mode coaching summary (SESSION_MODE_PHASE2.md section 9, build step 20).

Gemini only turns computed court numbers into text, as everywhere else. The
payload carries counts and percents only: no coordinates, no metre sigmas, no
names, no video times.

The guard differs from single-swing in exactly the ways section 9.2 lists:
* ``rally`` / ``rallies`` are allowed HERE (court mode measures rallies); the
  single-swing ban on them is untouched.
* Bans use word-boundary regexes, plus spin and any number with a distance unit.
* No ordinal allowlist: only payload numbers, and ``N%`` for percent keys.
* ``mph`` is always banned: court mode never measures ball speed.

Not called live in this phase: there is no route yet. Tests use FakeGeminiClient.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from collections.abc import Mapping
from typing import Any, Final

from app.feedback.gemini import (
    GUARD_FALLBACK_MESSAGE,
    MODEL_NAME,
    GeminiClient,
    enforce,
    generation_config,
    parse_gemini_response,
    round_payload_numbers,
)
from app.models.court import (
    CourtAnalysisResponse,
    CourtFeedbackBallSpeed,
    CourtFeedbackInput,
    CourtFeedbackLineCalls,
    CourtFeedbackRally,
    CourtFeedbackScore,
    CourtFeedbackServes,
    CourtFeedbackZone,
    CourtSessionStatus,
    DepthZone,
    LengthConfidence,
    LineVerdict,
    ScoreResponse,
    StabilityVerdict,
    WidthZone,
)
from app.models.enums import FeedbackSource
from app.models.feedback import CoachingFeedback, NumericGuardReport

#: Under this many landings the model is skipped: the numbers are too thin.
MIN_LANDINGS_FOR_GEMINI: Final[int] = 30
#: data_quality is "limited" above either share, or on a shaky camera.
LIMITED_TOO_CLOSE_PERCENT: Final[int] = 25
LIMITED_LOW_CONFIDENCE_RALLY_SHARE: Final[float] = 0.25

COURT_BANNED_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"km/h",
        r"\bm/s\b",
        r"\bkph\b",
        r"\brpm\b",
        r"\bspin rate\b",
        r"\brevolutions\b",
        r"\bmet(?:er|re)s?\b",
        r"\bfeet\b",
        r"\binch(?:es)?\b",
        r"\bspin\b",
        r"\byards?\b",
        # Brief G3: no bare "in" -- "151 in, 18 out" is a count, not inches.
        r"\d\s?(?:m|cm|mm|ft)\b",
        r"\bmph\b",
    )
)

COURT_SYSTEM_INSTRUCTION: Final[str] = (
    "You are a text formatter for a tennis app. You receive court statistics "
    "already computed by a deterministic system from one fixed camera. You must "
    "NEVER compute, estimate, infer, convert or invent any number. Every number "
    "you write must appear verbatim in the input JSON; percents only as given "
    "with a % sign. Never join two numbers with a hyphen. State rally numbers "
    "only from the `rally` object. Always call line-call and score numbers "
    "estimates. Do not explain what a `too_close` ball really was. Do not state "
    "any distance, size, uncertainty, speed or spin. Never present a score "
    "unless `score.included` is true. If `data_quality` is \"limited\", say the "
    "numbers are rougher than usual. Give one summary, up to two strengths and "
    "up to two improvements about placement, depth and consistency."
)


def _percent(part: float, whole: int) -> int:
    return int(round(100.0 * part / whole)) if whole > 0 else 0


def build_court_feedback_input(
    analysis: CourtAnalysisResponse, score: ScoreResponse | None = None
) -> CourtFeedbackInput:
    """Section 9.1, computed in Python from the wire body."""
    summary = analysis.summary
    n = summary.count
    counts = summary.verdict_counts
    in_n = counts.get(LineVerdict.IN, 0)
    out_n = counts.get(LineVerdict.OUT, 0)
    close_n = counts.get(LineVerdict.TOO_CLOSE_TO_CALL, 0)
    line_calls = CourtFeedbackLineCalls(
        in_=in_n,
        out=out_n,
        too_close=close_n,
        in_percent=_percent(in_n, n),
        out_percent=_percent(out_n, n),
        too_close_percent=_percent(close_n, n),
    )
    serves = [l for l in analysis.landings if l.is_serve and l.line_call is not None]
    lengths = sorted(r.length_hits for r in analysis.rallies)
    low = sum(1 for r in analysis.rallies if r.length_confidence is LengthConfidence.LOW)
    rally = CourtFeedbackRally(
        rallies=len(analysis.rallies),
        median_hits=statistics.median_low(lengths) if lengths else None,
        longest_hits=lengths[-1] if lengths else None,
        low_confidence_rallies=low,
    )
    if score is not None and score.status == "confirmed":
        score_part = CourtFeedbackScore(
            included=True,
            sets=[tuple(s) for s in score.current.sets],  # type: ignore[misc]
            games=score.current.games,
        )
    else:
        score_part = CourtFeedbackScore(
            included=False,
            reason="estimate_not_confirmed" if score is not None else "no_score",
        )
    limited = (
        line_calls.too_close_percent > LIMITED_TOO_CLOSE_PERCENT
        or analysis.camera.verdict is StabilityVerdict.SHAKY
        or (bool(analysis.rallies) and low / len(analysis.rallies) > LIMITED_LOW_CONFIDENCE_RALLY_SHARE)
    )
    return CourtFeedbackInput(
        court_engine_version=analysis.court_engine_version,
        is_estimate=True,
        data_quality="limited" if limited else "good",
        landings_counted=n,
        line_calls=line_calls,
        depth=[
            CourtFeedbackZone(
                zone=z, expected_percent=_percent(summary.depth_expected_counts.get(z, 0.0), n)
            )
            for z in DepthZone
        ],
        width=[
            CourtFeedbackZone(zone=z, percent=_percent(summary.width_counts.get(z, 0), n))
            for z in WidthZone
        ],
        serves=CourtFeedbackServes(
            in_=sum(1 for l in serves if l.line_call.verdict is LineVerdict.IN),  # type: ignore[union-attr]
            out=sum(1 for l in serves if l.line_call.verdict is LineVerdict.OUT),  # type: ignore[union-attr]
            too_close=sum(
                1 for l in serves if l.line_call.verdict is LineVerdict.TOO_CLOSE_TO_CALL  # type: ignore[union-attr]
            ),
        ),
        rally=rally,
        score=score_part,
        ball_speed=CourtFeedbackBallSpeed(
            mph=None, confidence="unavailable", reason="not_measured_in_court_mode"
        ),
    )


def court_payload(core: CourtFeedbackInput) -> dict[str, Any]:
    """The JSON the model sees: wire names (``in``), floats rounded."""
    return round_payload_numbers(core.model_dump(mode="json", by_alias=True))


def _walk(node: Any, out: set[str], key: str | None) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int):
        out.add(str(node))
        if key is not None and key.endswith("percent"):
            out.add(f"{node}%")
        return
    if isinstance(node, float):
        for places in range(3):
            out.add(f"{node:.{places}f}")
        return
    if isinstance(node, Mapping):
        for k, child in node.items():
            _walk(child, out, str(k))
        return
    if isinstance(node, list):
        for child in node:
            _walk(child, out, key)


def court_numeric_allowlist(payload: Mapping[str, Any]) -> frozenset[str]:
    """Payload numbers only (no ordinals), plus ``N%`` for percent keys."""
    out: set[str] = set()
    _walk(payload, out, None)
    return frozenset(out)


def find_court_banned(text: str) -> list[str]:
    """Banned units in court text. ``rally`` and ``rallies`` are allowed."""
    return sorted({m.group(0).lower() for p in COURT_BANNED_PATTERNS for m in p.finditer(text)})


def render_court_prompt(payload: Mapping[str, Any]) -> str:
    return (
        "Court statistics (estimates from one fixed camera):\n"
        + json.dumps(payload, indent=2, sort_keys=True)
    )


def court_template_summary(core: CourtFeedbackInput) -> str:
    """Deterministic text: only payload numbers, always marked as estimates."""
    calls = core.line_calls
    parts = [
        f"Estimated from {core.landings_counted} landings: {calls.in_} in, "
        f"{calls.out} out and {calls.too_close} too close to call."
    ]
    if core.rally.rallies and core.rally.longest_hits is not None:
        parts.append(
            f"{core.rally.rallies} rallies; the longest was {core.rally.longest_hits} hits."
        )
    if core.data_quality == "limited":
        parts.append("These numbers are rougher than usual for this video.")
    return " ".join(parts)


def _template(core: CourtFeedbackInput, guard: NumericGuardReport) -> CoachingFeedback:
    return CoachingFeedback(
        summary=court_template_summary(core),
        strengths=[],
        improvements=[],
        source=FeedbackSource.TEMPLATE,
        model=None,
        guard=guard,
        latency_ms=None,
    )


def generate_court_summary(
    analysis: CourtAnalysisResponse,
    client: GeminiClient,
    score: ScoreResponse | None = None,
) -> CoachingFeedback:
    """Numbers -> payload -> Gemini text -> court guard. Never raises.

    A refused session or fewer than ``MIN_LANDINGS_FOR_GEMINI`` landings never
    calls the model; any model failure falls back to the template.
    """
    core = build_court_feedback_input(analysis, score)
    skipped = NumericGuardReport(passed=True, fell_back_to_template=True, mph_rule="banned")
    if analysis.status is CourtSessionStatus.REFUSED or core.landings_counted < MIN_LANDINGS_FOR_GEMINI:
        return _template(core, skipped)

    payload = court_payload(core)
    failed = NumericGuardReport(passed=False, fell_back_to_template=True, mph_rule="banned")
    started = time.perf_counter()
    try:
        raw = client.generate(
            system_instruction=COURT_SYSTEM_INSTRUCTION,
            prompt=render_court_prompt(payload),
            config=generation_config(),
        )
    except Exception:  # noqa: BLE001 - a Gemini failure never fails the request
        return _template(core, failed)
    latency_ms = int((time.perf_counter() - started) * 1000)

    draft = parse_gemini_response(raw)
    if draft is None:
        return _template(core, failed)
    cleaned, report = enforce(
        draft, court_numeric_allowlist(payload), None, banned_finder=find_court_banned
    )
    if cleaned is None:
        return _template(core, report)
    return CoachingFeedback(
        summary=cleaned.summary,
        strengths=cleaned.strengths,
        improvements=cleaned.improvements,
        source=FeedbackSource.GEMINI if report.passed else FeedbackSource.GEMINI_PARTIAL,
        model=MODEL_NAME,
        guard=report,
        latency_ms=latency_ms,
    )


__all__ = [
    "COURT_BANNED_PATTERNS",
    "COURT_SYSTEM_INSTRUCTION",
    "GUARD_FALLBACK_MESSAGE",
    "MIN_LANDINGS_FOR_GEMINI",
    "build_court_feedback_input",
    "court_numeric_allowlist",
    "court_payload",
    "court_template_summary",
    "find_court_banned",
    "generate_court_summary",
    "render_court_prompt",
]
