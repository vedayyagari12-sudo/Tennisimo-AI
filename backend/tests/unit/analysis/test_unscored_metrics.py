"""shoulder_turn_deg, hip_rotation_deg, wrist_lag_deg and shoulder_hip_separation_deg:
computed, never scored.

Each measures something other than what its name says (in-plane tilt, not
turn; a forearm angle blind to the hand; a difference of two in-plane line
angles that reads 0 for a real X-factor), so none may reach a user as a scored
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
    shoulder_hip_separation_deg,
    turn_flag_warning,
)
from app.analysis.normalize import LEFT_HIP, LEFT_SHOULDER, RIGHT_HIP, RIGHT_SHOULDER
from app.analysis.phases import segment_swing_phases
from app.analysis.rubric import (
    CATEGORY_WEIGHTS,
    EXCLUDED_METRICS,
    METRIC_SPECS,
    MIN_SCORED_CATEGORIES,
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
    CategoryScore,
    MetricScore,
    Scorecard,
    ShotTypeInference,
    SwingMetrics,
)
from app.services.pipeline import (
    Accumulator,
    record_metric_warnings,
    scorecard_coverage_message,
)

from tests.unit.analysis.synthetic_swing import (
    RIGHT_HANDED,
    build_contact,
    build_sequence,
)

UNSCORED: tuple[str, ...] = (
    "shoulder_turn_deg",
    "hip_rotation_deg",
    "wrist_lag_deg",
    "shoulder_hip_separation_deg",
)

#: A real v4 payload persisted at HEAD before the withdrawal (serve_01, rubric
#: ``rubric_v0_placeholder``): its scorecard still scores shoulder_turn_deg and
#: hip_rotation_deg.
OLD_PAYLOAD_PATH: Path = (
    Path(__file__).resolve().parents[2] / "fixtures" / "payloads"
    / "v4_rubric_v0_placeholder_serve.json"
)

# Every metric inside the forehand_topspin band, AND the unscored ones set to
# values that would have scored badly under the old bands (0.0 is far below
# 80-110 / 35-60 / 15-45, -45.0 far below 40-65): if any still scored, the
# overall would drop.
IN_BAND_PLUS_UNSCORED: dict[str, float] = {
    "shoulder_hip_separation_deg": -45.0,
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


def test_the_four_are_unscored_diagnostics_with_a_reason_each() -> None:
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


@pytest.mark.parametrize("shot_type", list(ShotType))
def test_preparation_has_no_scored_metric_and_is_none_never_zero(shot_type: ShotType) -> None:
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), shot_type)
    preparation = next(c for c in card.categories if c.category == ScoreCategory.PREPARATION)
    assert preparation.metric_names == []
    assert preparation.score_0_100 is None
    assert preparation.metrics_available == 0
    assert preparation.metrics_total == 0
    # The category is still reported (all five always are), with its weight.
    assert len(card.categories) == len(ScoreCategory)


def test_a_full_forehand_still_gets_an_overall_score_from_the_other_four() -> None:
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), ShotType.FOREHAND_TOPSPIN)
    scored = [c for c in card.categories if c.score_0_100 is not None]
    assert len(scored) == 4
    assert card.overall_score == pytest.approx(100.0)


# --- why shoulder_hip_separation_deg is withdrawn (synthetic) -----------------


def _with_trunk(shoulder_half_width: float, *, swap_shoulders: bool = False) -> float | None:
    """Separation on the canonical swing with level hips and level shoulders."""
    seq = build_sequence()
    contact = build_contact()
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    seq.points[:, LEFT_HIP] = (-0.15, 0.0)
    seq.points[:, RIGHT_HIP] = (0.15, 0.0)
    left, right = (-shoulder_half_width, 1.0), (shoulder_half_width, 1.0)
    if swap_shoulders:
        left, right = right, left
    seq.points[:, LEFT_SHOULDER] = left
    seq.points[:, RIGHT_SHOULDER] = right
    return shoulder_hip_separation_deg(seq, phases)


def test_a_true_45_degree_x_factor_about_the_vertical_axis_reads_zero() -> None:
    """Shoulders turned 45 deg further than the hips, seen by a level camera.

    About the vertical axis the turn only shortens the projected shoulder line
    (0.2 * cos 45 deg); both lines stay horizontal, so the in-plane difference
    is 0.0 whatever the real X-factor is.
    """
    import math

    assert _with_trunk(0.2 * math.cos(math.radians(45.0))) == pytest.approx(0.0)


def test_a_shoulder_landmark_swap_reads_180() -> None:
    reading = _with_trunk(0.2, swap_shoulders=True)
    assert reading is not None
    assert abs(reading) == pytest.approx(180.0)


def test_stage_13_still_computes_them_as_diagnostics() -> None:
    seq = build_sequence()
    contact = build_contact()
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, phases)
    assert metrics.shoulder_hip_separation_deg is not None
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
                old_metric_row("shoulder_hip_separation_deg", ScoreCategory.PREPARATION),
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
    assert {"shoulder_turn_deg", "hip_rotation_deg", "shoulder_hip_separation_deg"} <= {
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
    assert response.metrics.shoulder_hip_separation_deg is not None
    assert "shoulder_hip_separation_deg" in {m.name for m in response.scorecard.metrics}
    # Round-trips unchanged: nothing in the model rejects or rewrites them.
    assert AnalysisResponse.model_validate(response.model_dump(mode="json")) == response


# --- the status message for what the scorecard could not score ---------------


def test_coverage_message_when_one_or_two_categories_scored_says_so() -> None:
    """It used to read "no scoring category had a measurable metric" -- false here."""
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), ShotType.SERVE)
    assert card.overall_score is None
    message = scorecard_coverage_message(card)
    assert message == (
        "only 2 of 5 scoring categories could be scored; an overall score needs "
        f"at least {MIN_SCORED_CATEGORIES}"
    )
    assert "no scoring category" not in message

    one = build_scorecard(
        SwingMetrics(follow_through_height_tu=0.60), ShotType.FOREHAND_TOPSPIN
    )
    assert one.overall_score is None
    assert scorecard_coverage_message(one) == (
        "only 1 of 5 scoring categories could be scored; an overall score needs "
        f"at least {MIN_SCORED_CATEGORIES}"
    )


def test_coverage_message_when_nothing_scored() -> None:
    card = build_scorecard(SwingMetrics(), ShotType.FOREHAND_TOPSPIN)
    assert scorecard_coverage_message(card) == "no scoring category had a measurable metric"


def test_coverage_message_with_an_overall_score_over_four_categories() -> None:
    card = build_scorecard(SwingMetrics(**IN_BAND_PLUS_UNSCORED), ShotType.FOREHAND_TOPSPIN)
    assert card.overall_score is not None
    assert scorecard_coverage_message(card) == "4 of 5 scoring categories were measurable"


def _category(category: ScoreCategory, score: float | None) -> CategoryScore:
    return CategoryScore(
        category=category,
        score_0_100=score,
        weight=CATEGORY_WEIGHTS[category],
        metric_names=[],
        metrics_available=0 if score is None else 1,
        metrics_total=1,
    )


def test_coverage_message_is_none_when_every_category_scored() -> None:
    card = Scorecard(
        overall_score=80.0,
        categories=[_category(category, 80.0) for category in CATEGORY_WEIGHTS],
        metrics=[],
    )
    assert scorecard_coverage_message(card) is None


def test_coverage_message_never_claims_too_few_when_enough_scored() -> None:
    """Unreachable with the shipped weights; still must not say something false."""
    scores = [80.0, 80.0, 80.0, None, None]
    card = Scorecard(
        overall_score=None,
        categories=[
            _category(category, score)
            for category, score in zip(CATEGORY_WEIGHTS, scores, strict=True)
        ],
        metrics=[],
    )
    assert scorecard_coverage_message(card) == (
        "no overall score although 3 of 5 scoring categories scored"
    )
