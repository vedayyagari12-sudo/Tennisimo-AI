"""shoulder_turn_deg, hip_rotation_deg and wrist_lag_deg: computed, never scored.

Each measures something other than what its name says (in-plane tilt, not
turn; a forearm angle blind to the hand), so none may reach a user as a scored
or coachable measurement: not in a band table, not in a category or the overall
score, not in ``Scorecard.metrics``, not in the Gemini payload, and its Stage 13
warnings neither show nor make an analysis ``partial``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.analysis.metrics import (
    HIP_LINE_FORESHORTENED,
    HIP_LINE_TOO_FEW_FRAMES,
    SHOULDER_LINE_FORESHORTENED,
    SHOULDER_LINE_TOO_FEW_FRAMES,
    compute_swing_metrics,
    turn_flag_warning,
)
from app.analysis.phases import segment_swing_phases
from app.analysis.rubric import (
    EXCLUDED_METRICS,
    METRIC_SPECS,
    UNSCORED_DIAGNOSTIC_METRICS,
    bands_for,
    is_unscored_metric_warning,
)
from app.analysis.scoring import build_scorecard
from app.feedback.gemini import build_feedback_payload, render_user_prompt
from app.feedback.projection import build_feedback_input, priority_metric_names
from app.models.enums import (
    BallSpeedConfidence,
    BallSpeedUnavailableReason,
    MetricUnit,
    MetricVerdict,
    ScoreCategory,
    ShotType,
)
from app.models.responses import (
    AnalysisResponse,
    BallSpeedResult,
    MetricScore,
    Scorecard,
    ShotTypeInference,
    SwingMetrics,
)
from app.services.pipeline import Accumulator, record_metric_warnings

from tests.unit.analysis.synthetic_swing import (
    RIGHT_HANDED,
    build_contact,
    build_sequence,
)

UNSCORED: tuple[str, ...] = ("shoulder_turn_deg", "hip_rotation_deg", "wrist_lag_deg")

#: A real v4 payload persisted at HEAD before the withdrawal (serve_01, rubric
#: ``rubric_v0_placeholder``): its scorecard still scores shoulder_turn_deg and
#: hip_rotation_deg.
OLD_PAYLOAD_PATH: Path = (
    Path(__file__).resolve().parents[2] / "fixtures" / "payloads"
    / "v4_rubric_v0_placeholder_serve.json"
)

# Every metric inside the forehand_topspin band, AND the three unscored ones
# set to values that would have scored badly under the old bands (0.0 is far
# below 80-110 / 35-60 / 15-45): if any still scored, the overall would drop.
IN_BAND_PLUS_UNSCORED: dict[str, float] = {
    "shoulder_hip_separation_deg": 52.0,
    "elbow_angle_at_contact_deg": 140.0,
    "contact_height_ratio": 0.90,
    "contact_point_forward_tu": 0.42,
    "peak_hand_speed_tu_s": 9.0,
    "swing_path_angle_deg": 25.0,
    "swing_plane_deviation_tu": 0.04,
    "knee_flexion_min_deg": 150.0,
    "follow_through_height_tu": 0.60,
    "head_stillness_tu": 0.03,
    "tempo_ratio": 2.2,
    "shoulder_turn_deg": 0.0,
    "hip_rotation_deg": 0.0,
    "wrist_lag_deg": 0.0,
}


def no_speed() -> BallSpeedResult:
    return BallSpeedResult(
        ball_speed_mph=None,
        confidence=BallSpeedConfidence.UNAVAILABLE,
        detections_used=0,
        unavailable_reason=BallSpeedUnavailableReason.NOT_CALIBRATED,
    )


def inferred(shot_type: ShotType) -> ShotTypeInference:
    return ShotTypeInference(
        shot_type=shot_type, confidence=0.7, class_scores={shot_type.value: 1.0}, evidence=[]
    )


def old_metric_row(name: str, category: ScoreCategory) -> MetricScore:
    """A stored, pre-withdrawal ``Scorecard.metrics`` row: measured and scored LOW."""
    return MetricScore(
        name=name,
        value=0.0,
        unit=MetricUnit.DEGREES,
        verdict=MetricVerdict.LOW,
        score_0_100=0.0,
        weight=0.30,
        ideal_min=80.0,
        ideal_max=110.0,
        category=category,
        view_sensitive=True,
    )


def load_old_payload() -> dict[str, Any]:
    return json.loads(OLD_PAYLOAD_PATH.read_text(encoding="utf-8"))


# --- rubric ------------------------------------------------------------------


def test_the_three_are_unscored_diagnostics_with_a_reason_each() -> None:
    assert set(UNSCORED_DIAGNOSTIC_METRICS) == set(UNSCORED)
    for name in UNSCORED:
        assert UNSCORED_DIAGNOSTIC_METRICS[name].strip()
        assert name in EXCLUDED_METRICS
        assert name not in METRIC_SPECS


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_no_band_table_contains_them(shot_type: ShotType) -> None:
    for name in UNSCORED:
        assert name not in bands_for(shot_type), (shot_type, name)


# --- scorecard ---------------------------------------------------------------


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_scorecard_excludes_them_even_when_measured(shot_type: ShotType) -> None:
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), shot_type)
    names = {metric.name for metric in card.metrics}
    for name in UNSCORED:
        assert name not in names
        for category in card.categories:
            assert name not in category.metric_names


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_their_values_never_move_any_score(shot_type: ShotType) -> None:
    measured = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), shot_type)
    absent = build_scorecard(
        SwingMetrics(**{**IN_BAND_PLUS_UNSCORED, **dict.fromkeys(UNSCORED, None)}), shot_type
    )
    assert measured.overall_score == absent.overall_score
    assert [c.score_0_100 for c in measured.categories] == [
        c.score_0_100 for c in absent.categories
    ]


def test_preparation_is_separation_alone() -> None:
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), ShotType.FOREHAND_TOPSPIN)
    preparation = next(c for c in card.categories if c.category == ScoreCategory.PREPARATION)
    assert preparation.metric_names == ["shoulder_hip_separation_deg"]
    assert preparation.score_0_100 == pytest.approx(100.0)


def test_stage_13_still_computes_them_as_diagnostics() -> None:
    seq = build_sequence()
    contact = build_contact()
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, phases)
    assert metrics.wrist_lag_deg is not None
    assert metrics.shoulder_turn_deg is not None
    assert metrics.hip_rotation_deg is not None


# --- Gemini payload ----------------------------------------------------------


def test_gemini_payload_never_carries_them() -> None:
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), ShotType.FOREHAND_TOPSPIN)
    core = build_feedback_input(
        scorecard=card,
        shot_type=inferred(ShotType.FOREHAND_TOPSPIN),
        handedness="right",
        contact_confidence=0.8,
        ball_speed=no_speed(),
    )
    prompt = render_user_prompt(build_feedback_payload(core))
    for name in UNSCORED:
        assert name not in prompt


def test_an_old_scorecard_that_still_scores_them_cannot_steer_gemini() -> None:
    """Second lock: a pre-withdrawal scorecard, where they score 0 (worst)."""
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), ShotType.FOREHAND_TOPSPIN)
    old = card.model_copy(
        update={
            "metrics": [
                *card.metrics,
                old_metric_row("shoulder_turn_deg", ScoreCategory.PREPARATION),
                old_metric_row("hip_rotation_deg", ScoreCategory.PREPARATION),
                old_metric_row("wrist_lag_deg", ScoreCategory.CONTACT),
            ]
        }
    )
    assert priority_metric_names(old) == []
    core = build_feedback_input(
        scorecard=old,
        shot_type=inferred(ShotType.FOREHAND_TOPSPIN),
        handedness="right",
        contact_confidence=0.8,
        ball_speed=no_speed(),
    )
    assert {m.name for m in core.metrics}.isdisjoint(UNSCORED)
    prompt = render_user_prompt(build_feedback_payload(core))
    for name in UNSCORED:
        assert name not in prompt


def test_the_stored_old_payload_cannot_steer_gemini() -> None:
    response = AnalysisResponse.model_validate(load_old_payload())
    assert {"shoulder_turn_deg", "hip_rotation_deg"} <= {
        m.name for m in response.scorecard.metrics
    }
    core = build_feedback_input(
        scorecard=response.scorecard,
        shot_type=response.shot_type,
        handedness=response.handedness.handedness.value,
        contact_confidence=response.contact.confidence,
        ball_speed=response.ball_speed,
    )
    assert {m.name for m in core.metrics}.isdisjoint(UNSCORED)
    assert set(core.priority_metric_names).isdisjoint(UNSCORED)


# --- warnings and status -----------------------------------------------------


@pytest.mark.parametrize(
    ("metric_name", "flag"),
    [
        ("shoulder_turn_deg", SHOULDER_LINE_FORESHORTENED),
        ("shoulder_turn_deg", SHOULDER_LINE_TOO_FEW_FRAMES),
        ("hip_rotation_deg", HIP_LINE_FORESHORTENED),
        ("hip_rotation_deg", HIP_LINE_TOO_FEW_FRAMES),
    ],
)
def test_their_flags_are_recognised_from_the_real_warning_text(
    metric_name: str, flag: str
) -> None:
    assert is_unscored_metric_warning(turn_flag_warning(metric_name, flag))


def test_other_stage_13_warnings_are_not_mistaken_for_them() -> None:
    assert not is_unscored_metric_warning(
        "low handedness confidence (0.14 < 0.5); user hint applied (source user_hint); "
        "handedness-sensitive metrics may be unreliable"
    )
    assert not is_unscored_metric_warning("swing metric computation failed internally (X: y)")


def test_their_flags_do_not_make_the_job_partial_or_reach_any_warning_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    accumulator = Accumulator()
    flags = [
        turn_flag_warning("shoulder_turn_deg", SHOULDER_LINE_FORESHORTENED),
        turn_flag_warning("hip_rotation_deg", HIP_LINE_TOO_FEW_FRAMES),
    ]
    with caplog.at_level("DEBUG", logger="tennisform.pipeline"):
        record_metric_warnings(accumulator, flags)
    assert accumulator.partial is False
    assert accumulator.warnings == []
    assert accumulator.low_confidence == []
    # Logged, not lost.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert SHOULDER_LINE_FORESHORTENED in logged
    assert HIP_LINE_TOO_FEW_FRAMES in logged


def test_any_other_metric_warning_still_makes_the_job_partial() -> None:
    accumulator = Accumulator()
    other = "low handedness confidence (0.14 < 0.5); handedness-sensitive metrics may be unreliable"
    record_metric_warnings(
        accumulator,
        [turn_flag_warning("shoulder_turn_deg", SHOULDER_LINE_FORESHORTENED), other],
    )
    assert accumulator.partial is True
    assert accumulator.warnings == [other]
    assert accumulator.low_confidence == [other]


# --- stored payloads ---------------------------------------------------------


def test_an_old_stored_payload_that_still_scores_them_validates() -> None:
    payload = load_old_payload()
    assert payload["scorecard"]["rubric_version"] == "rubric_v0_placeholder"
    response = AnalysisResponse.model_validate(payload)
    assert response.scorecard.rubric_version == "rubric_v0_placeholder"
    assert response.metrics.shoulder_turn_deg is not None
    # Round-trips unchanged: nothing in the model rejects or rewrites them.
    assert AnalysisResponse.model_validate(response.model_dump(mode="json")) == response
