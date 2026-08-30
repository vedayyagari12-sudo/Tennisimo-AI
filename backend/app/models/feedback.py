"""Pydantic models used by `app/feedback/`.

Minimal versions of the PIPELINE.md 2.x contract: only the pieces the feedback
stage reads or produces. `FeedbackInput` is a narrow stand-in for `AnalysisCore`
that carries exactly the already-computed numbers Gemini is allowed to see.
"""

from __future__ import annotations

from typing import Annotated, Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    FeedbackSource,
    MetricUnit,
    MetricVerdict,
)

#: The Gemini payload is a plain JSON-safe dict (PIPELINE.md Stage 16).
FeedbackPayload: TypeAlias = dict[str, Any]


class MetricScore(BaseModel):
    """One scored swing metric. `value is None` means unavailable, never zero."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None = None
    unit: MetricUnit
    verdict: MetricVerdict
    score: float | None = None
    ideal_min: float | None = None
    ideal_max: float | None = None
    view_sensitive: bool = False


class BallSpeedResult(BaseModel):
    """Ball speed measurement. Always present; the speed inside it is nullable."""

    model_config = ConfigDict(extra="forbid")

    ball_speed_mph: Annotated[StrictInt, Field(ge=15, le=160)] | None = Field(
        default=None,
        description=(
            "Whole miles per hour, or null. StrictInt: a float is a validation "
            "error, not a coercion. NEVER a fabricated or best-guess number."
        ),
    )
    confidence: BallSpeedConfidence = BallSpeedConfidence.UNAVAILABLE
    detections_used: int = Field(default=0, ge=0)
    unavailable_reason: BallSpeedUnavailableReason | None = Field(
        default=None, description="Non-null iff ball_speed_mph is null."
    )


class FeedbackInput(BaseModel):
    """Everything the feedback stage is allowed to consume.

    Structurally excludes video, frames, landmarks, user identity and calibration
    tap coordinates: they simply have no field here.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_version: str = "v2"
    rubric_version: str = "rubric_v1"

    shot_type: str
    shot_type_confidence: float = Field(ge=0.0, le=1.0)
    shot_type_evidence: list[str] = Field(default_factory=list)
    handedness: str = "unknown"

    contact_confidence: float = Field(ge=0.0, le=1.0)
    truncated_clip: bool = False

    overall_score: float | None = None
    category_scores: dict[str, float] = Field(default_factory=dict)
    metrics: list[MetricScore] = Field(default_factory=list)
    ball_speed: BallSpeedResult = Field(default_factory=BallSpeedResult)
    priority_metric_names: list[str] = Field(default_factory=list)
    low_confidence_warnings: list[str] = Field(default_factory=list)


class Improvement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1, le=3)
    title: str
    why: str = Field(description="Prose. Contains only numbers from the payload.")
    cue: str = Field(description="One short on-court cue.")
    drill: str
    metric_refs: list[str] = Field(default_factory=list)


class GeminiFeedbackDraft(BaseModel):
    """Untrusted model output, before the numeric guard runs."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    strengths: list[str] = Field(default_factory=list)
    improvements: list[Improvement] = Field(default_factory=list)


class NumericGuardReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    rejected_tokens: list[str] = Field(default_factory=list)
    fields_discarded: list[str] = Field(default_factory=list)
    fell_back_to_template: bool = False
    mph_rule: str = Field(
        default="banned",
        description=(
            "'banned' when ball_speed_mph is null; 'bound_to:<int>' when it is not."
        ),
    )


class CoachingFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    strengths: list[str] = Field(default_factory=list)
    improvements: list[Improvement] = Field(default_factory=list)
    source: FeedbackSource
    model: str | None = Field(
        default=None, description="e.g. 'gemini-2.5-flash'. None if template."
    )
    guard: NumericGuardReport
    latency_ms: int | None = None
