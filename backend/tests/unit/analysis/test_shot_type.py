"""Stage 14 unit tests. Synthetic data only.

Each class fixture is hand-built to satisfy exactly that class's rule, so the
expected answer is known before the code runs. ``unknown`` gets more tests than
any single class because it is a first-class outcome, not a fallback.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import numpy as np
import pytest

from app.analysis import shot_type as shot_type_module
from app.analysis.metrics import compute_swing_metrics
from app.analysis.phases import segment_swing_phases
from app.analysis.normalize import LEFT_SHOULDER, LEFT_WRIST, NOSE, RIGHT_WRIST
from app.analysis.shot_type import (
    FOREHAND_BACKHAND_MIN_PROJECTION_TU,
    MARGIN_MIN,
    REAL_CLASSES,
    SLICE_FOLLOW_THROUGH_TU,
    TOP_SCORE_MIN,
    apply_acceptance_gates,
    infer_shot_type,
    normalize_scores,
    racket_wrist_for,
    shoulder_axis_projection_tu,
    swing_duration_s,
    swing_path_length_tu,
    wrist_above_nose,
    wrist_separations_near,
)
from app.models.enums import Handedness, ShotType
from app.models.responses import ShotTypeInference, SwingMetrics

from tests.unit.analysis.synthetic_swing import (
    CONTACT_FRAME,
    HINTED_RIGHT_HANDED,
    RIGHT_HANDED,
    UNKNOWN_HANDED,
    WEAK_RIGHT_HANDED,
    build_contact,
    build_sequence,
    canonical_wrist_path,
    hide,
    hide_all,
    hide_frames,
    unusable_contact,
)

FOREHAND_EVIDENCE = "weak evidence for a forehand"
TWO_HANDED_RULE_ABSTAINED = "the two-handed rule contributed nothing"


def classify(seq, handedness=RIGHT_HANDED, contact_frame: int = CONTACT_FRAME):
    """Run Stages 12 -> 13 -> 14 the way the orchestrator will."""
    contact = build_contact(frame_index=contact_frame)
    phases, _ = segment_swing_phases(seq, handedness, contact)
    metrics, _ = compute_swing_metrics(seq, handedness, contact, phases)
    return infer_shot_type(metrics, seq, handedness, contact, phases), metrics, phases


# --- per-class fixtures ------------------------------------------------------


def topspin_forehand_sequence():
    """The canonical clip: rising path, high finish, one hand, racket side."""
    return build_sequence()


def slice_forehand_sequence():
    """Same geometry, descending path and a low finish."""
    return build_sequence(wrist_xy=canonical_wrist_path(rise_tu=-0.9, base_y=1.5))


def serve_sequence():
    """Contact well above the shoulder line and above the nose."""
    return build_sequence(wrist_xy=canonical_wrist_path(rise_tu=2.2, base_y=0.6))


def two_handed_sequence():
    """Both wrists travel together, 0.15 TU apart, for the whole clip."""
    path = canonical_wrist_path()
    return build_sequence(wrist_xy=path, off_wrist_xy=path + np.array([0.0, -0.15]))


def one_handed_backhand_sequence():
    """The canonical swing mirrored: the hand crosses the body."""
    path = canonical_wrist_path() * np.array([-1.0, 1.0])
    return build_sequence(wrist_xy=path, swing_direction_sign=-1)


def volley_sequence():
    """A 10-frame punch: 0.52 TU of hand path in 0.27 s."""
    x = np.array([0.3, 0.3, 0.25, 0.2, 0.3, 0.45, 0.55, 0.6, 0.62, 0.63])
    y = np.full(10, 0.9)
    return build_sequence(frame_count=10, wrist_xy=np.stack([x, y], axis=1))


# --- measurement helpers -----------------------------------------------------


def test_racket_wrist_for_reports_unknown_rather_than_defaulting() -> None:
    assert racket_wrist_for(Handedness.RIGHT) == RIGHT_WRIST
    assert racket_wrist_for(Handedness.LEFT) == LEFT_WRIST
    assert racket_wrist_for(Handedness.UNKNOWN) is None


def test_wrist_separations_near_excludes_unseen_frames_rather_than_failing_them() -> None:
    seq = two_handed_sequence()
    assert len(wrist_separations_near(seq, CONTACT_FRAME)) == 7
    hidden = hide_frames(seq, [CONTACT_FRAME - 1, CONTACT_FRAME + 2], LEFT_WRIST)
    assert len(wrist_separations_near(hidden, CONTACT_FRAME)) == 5
    assert wrist_separations_near(hide_all(seq), CONTACT_FRAME) == []


def test_shoulder_axis_projection_signs_the_torso_axis_not_the_target_axis() -> None:
    forehand = shoulder_axis_projection_tu(
        topspin_forehand_sequence(), Handedness.RIGHT, CONTACT_FRAME
    )
    backhand = shoulder_axis_projection_tu(
        one_handed_backhand_sequence(), Handedness.RIGHT, CONTACT_FRAME
    )
    assert forehand is not None and forehand > FOREHAND_BACKHAND_MIN_PROJECTION_TU
    assert backhand is not None and backhand < -FOREHAND_BACKHAND_MIN_PROJECTION_TU
    assert forehand == pytest.approx(-backhand)
    assert (
        shoulder_axis_projection_tu(
            topspin_forehand_sequence(), Handedness.UNKNOWN, CONTACT_FRAME
        )
        is None
    )
    assert (
        shoulder_axis_projection_tu(
            hide(topspin_forehand_sequence(), LEFT_SHOULDER), Handedness.RIGHT, CONTACT_FRAME
        )
        is None
    )


def test_wrist_above_nose_uses_y_positive_up() -> None:
    assert wrist_above_nose(serve_sequence(), Handedness.RIGHT, CONTACT_FRAME) is True
    assert (
        wrist_above_nose(topspin_forehand_sequence(), Handedness.RIGHT, CONTACT_FRAME)
        is False
    )
    assert (
        wrist_above_nose(hide(serve_sequence(), NOSE), Handedness.RIGHT, CONTACT_FRAME)
        is None
    )


def test_swing_path_length_is_none_when_the_wrist_was_never_seen() -> None:
    """An unseen wrist would otherwise measure a perfect 0.0 TU -- a false volley."""
    seq = topspin_forehand_sequence()
    _, _, phases = classify(seq)
    assert swing_path_length_tu(seq, Handedness.RIGHT, phases) == pytest.approx(2.87, abs=0.05)
    assert swing_path_length_tu(seq, Handedness.RIGHT, None) is None
    assert swing_path_length_tu(seq, Handedness.UNKNOWN, phases) is None
    assert swing_path_length_tu(hide(seq, RIGHT_WRIST), Handedness.RIGHT, phases) is None


def test_swing_duration_requires_phases() -> None:
    _, _, phases = classify(topspin_forehand_sequence())
    assert swing_duration_s(phases) == pytest.approx(23.0 / 30.0)
    assert swing_duration_s(None) is None


def test_normalize_scores_keeps_all_zero_rather_than_inventing_a_uniform_prior() -> None:
    zeros = {member: 0.0 for member in REAL_CLASSES}
    assert normalize_scores(zeros) == {member.value: 0.0 for member in REAL_CLASSES}
    # ...and a real distribution does sum to 1.0.
    scored = dict(zeros)
    scored[ShotType.SERVE] = 3.0
    scored[ShotType.VOLLEY] = 1.0
    normalized = normalize_scores(scored)
    assert sum(normalized.values()) == pytest.approx(1.0)
    assert normalized[ShotType.SERVE.value] == pytest.approx(0.75)


def test_acceptance_gates_report_the_honest_top_score_even_when_rejecting() -> None:
    clear = {member.value: 0.0 for member in REAL_CLASSES}
    clear[ShotType.SERVE.value] = 0.6
    clear[ShotType.VOLLEY.value] = 0.4
    assert apply_acceptance_gates(clear) == (ShotType.SERVE, 0.6)

    thin = {member.value: 0.0 for member in REAL_CLASSES}
    thin[ShotType.SERVE.value] = TOP_SCORE_MIN - 0.04
    thin[ShotType.VOLLEY.value] = 0.1
    shot, confidence = apply_acceptance_gates(thin)
    assert shot == ShotType.UNKNOWN
    assert confidence == pytest.approx(TOP_SCORE_MIN - 0.04)  # not 0.0, not 1 - x

    tie = {member.value: 0.0 for member in REAL_CLASSES}
    tie[ShotType.SERVE.value] = 0.5
    tie[ShotType.VOLLEY.value] = 0.5 - (MARGIN_MIN / 2.0)
    assert apply_acceptance_gates(tie)[0] == ShotType.UNKNOWN


# --- one clip per class ------------------------------------------------------


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (topspin_forehand_sequence, ShotType.FOREHAND_TOPSPIN),
        (slice_forehand_sequence, ShotType.FOREHAND_SLICE),
        (serve_sequence, ShotType.SERVE),
        (two_handed_sequence, ShotType.BACKHAND_TWO_HANDED),
    ],
)
def test_each_reachable_class_is_identified_with_confidence(builder, expected) -> None:
    result, _, _ = classify(builder())
    assert result.shot_type == expected
    assert result.confidence >= TOP_SCORE_MIN
    assert sum(result.class_scores.values()) == pytest.approx(1.0)


def test_volley_is_identified_with_confidence() -> None:
    result, _, _ = classify(volley_sequence(), contact_frame=5)
    assert result.shot_type == ShotType.VOLLEY
    assert result.confidence >= TOP_SCORE_MIN


def test_one_handed_backhand_is_the_top_class_but_is_not_confidently_called() -> None:
    """A documented limitation, asserted so it cannot regress silently.

    The only axis separating a one-handed backhand from a forehand is the
    capped soft evidence of the forehand/backhand proxy, so the class cannot
    clear the acceptance gate. ``unknown`` is the correct, honest answer.
    """
    result, _, _ = classify(one_handed_backhand_sequence())
    top = max(result.class_scores.items(), key=lambda item: item[1])
    assert top[0] == ShotType.BACKHAND_ONE_HANDED.value
    assert result.shot_type == ShotType.UNKNOWN
    assert result.confidence == pytest.approx(top[1])
    assert result.confidence < TOP_SCORE_MIN


# --- unknown is first class --------------------------------------------------


def test_no_measurement_at_all_yields_all_zero_scores_not_a_uniform_prior() -> None:
    result = infer_shot_type(
        SwingMetrics(), hide_all(build_sequence()), UNKNOWN_HANDED, build_contact(), None
    )
    assert result.shot_type == ShotType.UNKNOWN
    assert result.confidence == 0.0
    assert set(result.class_scores.values()) == {0.0}
    assert result.class_scores != {member.value: 1.0 / 6.0 for member in REAL_CLASSES}
    assert any("no discriminating measurement" in text for text in result.evidence)


def test_the_slice_conjunct_is_never_dropped_when_one_term_is_none() -> None:
    """The guard against PIPELINE.md:989's two-term rule becoming a one-term rule.

    ``swing_path_angle_deg`` alone says slice; the second conjunct is missing,
    so the axis must contribute NOTHING rather than classify on one term.
    """
    seq = slice_forehand_sequence()
    contact = build_contact()
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, phases)
    assert metrics.swing_path_angle_deg is not None
    assert metrics.swing_path_angle_deg < -10.0

    crippled = metrics.model_copy(update={"follow_through_height_tu": None})
    result = infer_shot_type(crippled, seq, RIGHT_HANDED, contact, phases)
    assert result.shot_type != ShotType.FOREHAND_SLICE
    assert any("follow_through_height_tu unavailable" in text for text in result.evidence)

    # And the mirror: the height alone must not classify either.
    other = metrics.model_copy(update={"swing_path_angle_deg": None})
    assert infer_shot_type(other, seq, RIGHT_HANDED, contact, phases).shot_type != (
        ShotType.FOREHAND_SLICE
    )


def test_a_flat_path_produces_no_spin_qualifier_and_falls_to_unknown() -> None:
    seq = topspin_forehand_sequence()
    contact = build_contact()
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, phases)
    flat = metrics.model_copy(
        update={"swing_path_angle_deg": 2.0, "follow_through_height_tu": 0.25}
    )
    result = infer_shot_type(flat, seq, RIGHT_HANDED, contact, phases)
    assert result.shot_type == ShotType.UNKNOWN
    assert result.class_scores[ShotType.FOREHAND_TOPSPIN.value] == pytest.approx(
        result.class_scores[ShotType.FOREHAND_SLICE.value]
    )


# --- handedness degradation --------------------------------------------------


@pytest.mark.parametrize(
    ("handedness", "expect_evidence"),
    [
        (RIGHT_HANDED, True),
        (WEAK_RIGHT_HANDED, False),
        (HINTED_RIGHT_HANDED, True),
        (UNKNOWN_HANDED, False),
    ],
)
def test_forehand_backhand_evidence_degrades_with_the_handedness_read(
    handedness, expect_evidence: bool
) -> None:
    """Identical clip and identical metrics; only the handedness read changes.

    A weak DETECTED read contributes nothing; the same weak read WITH a user
    hint does contribute, because a correct hint is the precondition for
    trusting anything handedness-sensitive.
    """
    seq = topspin_forehand_sequence()
    contact = build_contact()
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, contact)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, phases)
    result = infer_shot_type(metrics, seq, handedness, contact, phases)
    fired = any(FOREHAND_EVIDENCE in text for text in result.evidence)
    assert fired is expect_evidence
    if not expect_evidence:
        assert result.shot_type == ShotType.UNKNOWN


# --- the two-handed sustain --------------------------------------------------


def test_hands_that_touch_only_at_contact_are_not_two_handed() -> None:
    path = canonical_wrist_path()
    off_hand = path + np.array([0.0, -0.15])
    # Pull the off hand away everywhere except the contact frame itself.
    off_hand[:CONTACT_FRAME] += np.array([0.0, -1.2])
    off_hand[CONTACT_FRAME + 1 :] += np.array([0.0, -1.2])
    seq = build_sequence(wrist_xy=path, off_wrist_xy=off_hand)
    result, _, _ = classify(seq)
    assert result.shot_type != ShotType.BACKHAND_TWO_HANDED
    assert result.class_scores[ShotType.BACKHAND_TWO_HANDED.value] == 0.0


def test_the_two_handed_rule_abstains_rather_than_firing_on_three_frames() -> None:
    seq = two_handed_sequence()
    occluded = hide_frames(
        seq,
        [CONTACT_FRAME - 3, CONTACT_FRAME - 2, CONTACT_FRAME + 2, CONTACT_FRAME + 3],
        LEFT_WRIST,
    )
    result, _, _ = classify(occluded)
    assert any(TWO_HANDED_RULE_ABSTAINED in text for text in result.evidence)
    assert result.class_scores[ShotType.BACKHAND_TWO_HANDED.value] == 0.0


# --- structural ball independence -------------------------------------------


def test_infer_shot_type_takes_no_ball_argument() -> None:
    signature = inspect.signature(infer_shot_type)
    for parameter in signature.parameters.values():
        assert "ball" not in str(parameter.annotation).lower(), parameter.name
        assert "ball" not in parameter.name.lower()


def test_the_module_imports_nothing_from_app_ball() -> None:
    """Read the import graph, not the behaviour.

    The behaviour cannot be tested for the ABSENCE of an influence; the import
    graph can.
    """
    tree = ast.parse(Path(shot_type_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not any(
        name == "app.ball" or name.startswith("app.ball.") for name in imported
    )
    assert not any("speed" in name for name in imported)


# --- never raises ------------------------------------------------------------


def test_never_raises_on_degenerate_input() -> None:
    cases = [
        (SwingMetrics(), build_sequence(), RIGHT_HANDED, build_contact(), None),
        (SwingMetrics(), build_sequence(frame_count=0), RIGHT_HANDED, build_contact(), None),
        (SwingMetrics(), build_sequence(), UNKNOWN_HANDED, unusable_contact(), None),
        (
            SwingMetrics(contact_height_ratio=float("nan")),
            build_sequence(),
            RIGHT_HANDED,
            build_contact(frame_index=999),
            None,
        ),
    ]
    for metrics, seq, handedness, contact, phases in cases:
        result = infer_shot_type(metrics, seq, handedness, contact, phases)
        assert isinstance(result, ShotTypeInference)
        assert 0.0 <= result.confidence <= 1.0
        assert set(result.class_scores) == {member.value for member in REAL_CLASSES}


def test_phases_none_disables_volley_detection_rather_than_guessing() -> None:
    seq = volley_sequence()
    contact = build_contact(frame_index=5)
    metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact, None)
    result = infer_shot_type(metrics, seq, RIGHT_HANDED, contact, None)
    assert result.shot_type != ShotType.VOLLEY
    assert any("swing duration unavailable" in text for text in result.evidence)


def test_slice_threshold_constant_is_the_documented_replacement_conjunct() -> None:
    """Pins the one entirely-new number in this module so a change is visible."""
    assert SLICE_FOLLOW_THROUGH_TU == 0.20
