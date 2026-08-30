"""Feedback stage: reference comparison + Gemini prose generation."""

from app.feedback.gemini import (
    GeminiClient,
    GoogleGenAIClient,
    generate_feedback,
    should_skip_gemini,
)
from app.feedback.references import (
    REFERENCE_RANGES,
    Deviation,
    DeviationDirection,
    ReferenceRange,
    ReferenceShotType,
    compare_to_reference,
)

__all__ = [
    "REFERENCE_RANGES",
    "Deviation",
    "DeviationDirection",
    "GeminiClient",
    "GoogleGenAIClient",
    "ReferenceRange",
    "ReferenceShotType",
    "compare_to_reference",
    "generate_feedback",
    "should_skip_gemini",
]
