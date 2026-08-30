"""Shared fixtures: synthetic analysis cores and a fake, offline Gemini client.

No test in this repo may make a network call. ``FakeGeminiClient`` records every
invocation so the skip path can assert the client was never invoked.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    MetricUnit,
    MetricVerdict,
)
from app.models.feedback import BallSpeedResult, FeedbackInput, MetricScore


class FakeGeminiClient:
    """Offline stand-in for ``GeminiClient``. Returns a canned raw response."""

    def __init__(self, response: str = "", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        config: dict[str, Any],
    ) -> str:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "prompt": prompt,
                "config": config,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture()
def fake_client() -> FakeGeminiClient:
    return FakeGeminiClient()


def make_metrics() -> list[MetricScore]:
    """Synthetic metric scores: one low, one ideal, one unavailable."""
    return [
        MetricScore(
            name="shoulder_hip_separation_deg",
            value=38.4,
            unit=MetricUnit.DEGREES,
            verdict=MetricVerdict.LOW,
            score=64.0,
            ideal_min=45.0,
            ideal_max=65.0,
            view_sensitive=False,
        ),
        MetricScore(
            name="contact_height_ratio",
            value=0.86,
            unit=MetricUnit.RATIO,
            verdict=MetricVerdict.IDEAL,
            score=100.0,
            ideal_min=0.75,
            ideal_max=1.05,
            view_sensitive=False,
        ),
        MetricScore(
            name="knee_flexion_min_deg",
            value=None,
            unit=MetricUnit.DEGREES,
            verdict=MetricVerdict.UNAVAILABLE,
            score=None,
            ideal_min=135.0,
            ideal_max=160.0,
            view_sensitive=True,
        ),
    ]


def make_core(
    *,
    ball_speed_mph: int | None = 68,
    contact_confidence: float = 0.74,
    truncated_clip: bool = False,
) -> FeedbackInput:
    """Build a synthetic ``FeedbackInput`` matching the Stage 16 example."""
    if ball_speed_mph is None:
        ball_speed = BallSpeedResult(
            ball_speed_mph=None,
            confidence=BallSpeedConfidence.UNAVAILABLE,
            detections_used=0,
            unavailable_reason=BallSpeedUnavailableReason.NOT_CALIBRATED,
        )
    else:
        ball_speed = BallSpeedResult(
            ball_speed_mph=ball_speed_mph,
            confidence=BallSpeedConfidence.MEDIUM,
            detections_used=6,
            unavailable_reason=None,
        )
    return FeedbackInput(
        shot_type="forehand_topspin",
        shot_type_confidence=0.81,
        shot_type_evidence=["contact on dominant side of mid-hip"],
        handedness="right",
        contact_confidence=contact_confidence,
        truncated_clip=truncated_clip,
        overall_score=71.5,
        category_scores={
            "preparation": 78.0,
            "contact": 64.0,
            "swing_path": 82.0,
            "balance": 69.0,
            "follow_through": 64.5,
        },
        metrics=make_metrics(),
        ball_speed=ball_speed,
        priority_metric_names=["shoulder_hip_separation_deg"],
        low_confidence_warnings=[],
    )


@pytest.fixture()
def core_with_speed() -> FeedbackInput:
    return make_core(ball_speed_mph=68)


@pytest.fixture()
def core_without_speed() -> FeedbackInput:
    return make_core(ball_speed_mph=None)
