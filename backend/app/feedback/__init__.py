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
    compare_to_reference,
    reference_ranges_for,
)

__all__ = [
    "REFERENCE_RANGES",
    "Deviation",
    "DeviationDirection",
    "GeminiClient",
    "GoogleGenAIClient",
    "ReferenceRange",
    "compare_to_reference",
    "generate_feedback",
    "reference_ranges_for",
    "should_skip_gemini",
]
