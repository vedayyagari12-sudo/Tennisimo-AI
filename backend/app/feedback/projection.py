"""Stage 16: project the wire scorecard onto the narrow Gemini payload.

PURE. Value objects in, value objects out. No I/O, no clock, no network.

WHY THIS MODULE EXISTS RATHER THAN A ``model_dump()`` PASSTHROUGH
----------------------------------------------------------------
``app.models.feedback.MetricScore`` and ``app.models.responses.MetricScore`` are
two DIFFERENT models with the same name, and they are incompatible in both
directions:

* the feedback model sets ``extra="forbid"`` (``feedback.py:29``);
* it names the field ``score``, the wire model names it ``score_0_100``
  (``feedback.py:35`` vs ``responses.py:256``);
* the wire model additionally carries ``weight`` and ``category``
  (``responses.py:257,260``), which have no home in the feedback model.

So ``feedback.MetricScore(**wire.model_dump())`` raises a ``ValidationError`` --
on ``weight`` and ``category`` as extras, and on the missing ``score``. Assert
that it raises; ``test_projection.py`` does, because testing the failure mode is
what stops someone reintroducing the passthrough.

**``extra="forbid"`` is a feature, not an annoyance.** It is what keeps rubric
internals -- weights, category structure -- out of the Gemini payload, which
PIPELINE.md:1028 requires to be "only numbers, enum strings, and verdicts". The
fix is this hand-written projection. It is NOT relaxing ``extra`` and it is NOT
renaming the field.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.analysis.rubric import CATEGORY_WEIGHTS, METRIC_SPECS, RUBRIC_VERSION
from app.models.enums import MetricVerdict
from app.models.feedback import BallSpeedResult as FeedbackBallSpeed
from app.models.feedback import FeedbackInput
from app.models.feedback import MetricScore as FeedbackMetricScore
from app.models.responses import BallSpeedResult as WireBallSpeed
from app.models.responses import MetricScore as WireMetricScore
from app.models.responses import Scorecard, ShotTypeInference

#: ``Improvement.priority`` is ``ge=1, le=3`` (``feedback.py:91``), so a fourth
#: priority metric has nowhere to go.
MAX_PRIORITY_METRICS: Final[int] = 3

#: Only these verdicts are coachable: a metric scoring 100 is not an improvement
#: area, and an ``unavailable`` metric has no number for Gemini to talk about.
_COACHABLE: Final[frozenset[MetricVerdict]] = frozenset(
    {MetricVerdict.LOW, MetricVerdict.HIGH}
)


def to_feedback_metric_score(metric: WireMetricScore) -> FeedbackMetricScore:
    """``responses.MetricScore`` -> ``feedback.MetricScore``.

    Renames ``score_0_100`` to ``score`` and DROPS ``weight`` and ``category``.
    Everything else is carried through unchanged. Written field by field rather
    than by dict surgery so that adding a field to either model is a type error
    here instead of a silent omission.
    """
    return FeedbackMetricScore(
        name=metric.name,
        value=metric.value,
        unit=metric.unit,
        verdict=metric.verdict,
        score=metric.score_0_100,
        ideal_min=metric.ideal_min,
        ideal_max=metric.ideal_max,
        view_sensitive=metric.view_sensitive,
    )


def to_feedback_ball_speed(result: WireBallSpeed) -> FeedbackBallSpeed:
    """Wire ``BallSpeedResult`` -> the feedback one. Same four fields, two models."""
    return FeedbackBallSpeed(
        ball_speed_mph=result.ball_speed_mph,
        confidence=result.confidence,
        detections_used=result.detections_used,
        unavailable_reason=result.unavailable_reason,
    )


def _rubric_weight(name: str) -> float:
    """``category weight x metric weight``, the tie-break rank. 0.0 if unscored."""
    spec = METRIC_SPECS.get(name)
    if spec is None:
        return 0.0
    return float(CATEGORY_WEIGHTS.get(spec.category, 0.0)) * float(spec.weight)


def priority_metric_names(
    scorecard: Scorecard, *, limit: int = MAX_PRIORITY_METRICS
) -> list[str]:
    """The metrics Gemini is steered to coach, worst first (design C.8).

    1. Only ``low`` / ``high`` verdicts -- available AND outside band.
    2. Rank by ``score_0_100`` ascending: worst first.
    3. **View-sensitivity demotion.** A metric flagged ``view_sensitive`` sorts
       after an equally-bad view-insensitive one. ``PIPELINE.md:945`` calls that
       flag the load-bearing mitigation for camera-angle error, and
       ``estimated_camera_view`` is hardcoded ``UNKNOWN``
       (``normalize.py:325``), so we can never confirm the view was good. It is
       a demotion and NOT an exclusion: 9 of 17 entries in
       ``METRIC_VIEW_SENSITIVE`` are ``True``, so excluding them could empty the
       list entirely.
    4. Tie-broken by rubric weight descending.
    5. Capped at three.

    An empty list is legal and correct when everything measurable is in band.
    """
    coachable = [
        metric
        for metric in scorecard.metrics
        if metric.verdict in _COACHABLE and metric.score_0_100 is not None
    ]
    coachable.sort(
        key=lambda m: (
            float(m.score_0_100 or 0.0),
            bool(m.view_sensitive),
            -_rubric_weight(m.name),
            m.name,
        )
    )
    return [metric.name for metric in coachable[: max(int(limit), 0)]]


def build_feedback_input(
    *,
    scorecard: Scorecard,
    shot_type: ShotTypeInference,
    handedness: str,
    contact_confidence: float,
    ball_speed: WireBallSpeed,
    truncated_clip: bool = False,
    low_confidence_warnings: Sequence[str] = (),
    pipeline_version: str = "v2",
) -> FeedbackInput:
    """Assemble the one object the feedback stage is allowed to consume.

    ``FeedbackInput`` sets ``extra="forbid"`` and has no field for video,
    frames, landmarks, user identity or calibration taps -- the exclusion is
    structural, not a convention this function upholds.

    ``rubric_version`` is taken from :data:`app.analysis.rubric.RUBRIC_VERSION`
    rather than from the model default, so the payload and the persisted
    ``Scorecard`` cannot disagree about which rubric produced the numbers.
    """
    category_scores = {
        category.category.value: float(category.score_0_100)
        for category in scorecard.categories
        if category.score_0_100 is not None
    }
    return FeedbackInput(
        pipeline_version=pipeline_version,
        rubric_version=RUBRIC_VERSION,
        shot_type=shot_type.shot_type.value,
        shot_type_confidence=shot_type.confidence,
        shot_type_evidence=list(shot_type.evidence),
        handedness=handedness,
        contact_confidence=contact_confidence,
        truncated_clip=truncated_clip,
        overall_score=scorecard.overall_score,
        category_scores=category_scores,
        metrics=[to_feedback_metric_score(metric) for metric in scorecard.metrics],
        ball_speed=to_feedback_ball_speed(ball_speed),
        priority_metric_names=priority_metric_names(scorecard),
        low_confidence_warnings=list(low_confidence_warnings),
    )


__all__: Sequence[str] = (
    "MAX_PRIORITY_METRICS",
    "build_feedback_input",
    "priority_metric_names",
    "to_feedback_ball_speed",
    "to_feedback_metric_score",
)
