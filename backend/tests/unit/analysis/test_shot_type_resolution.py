"""``resolve_shot_type``: the user's declaration vs the technique classifier.

Synthetic ``ShotTypeInference`` values only; the resolver never looks at
keypoints, so building a classifier result by hand is the whole fixture.
"""

from __future__ import annotations

import pytest

from app.analysis.rubric import RUBRIC_V1, UNBANDED_SHOT_TYPES
from app.analysis.shot_type import RULE_VERSION, resolve_shot_type
from app.models.enums import ShotType, ShotTypeSource
from app.models.responses import ShotTypeInference
from app.services.pipeline import MIN_CONTACT_CONFIDENCE

STRONG_CONTACT: float = 0.80
WEAK_CONTACT: float = MIN_CONTACT_CONFIDENCE - 0.01


def classifier(shot_type: ShotType, confidence: float = 0.72) -> ShotTypeInference:
    """What ``infer_shot_type`` would return; ``class_scores`` is illustrative."""
    return ShotTypeInference(
        shot_type=shot_type,
        confidence=confidence,
        class_scores={member.value: 0.0 for member in ShotType if member is not ShotType.UNKNOWN}
        | ({shot_type.value: confidence} if shot_type is not ShotType.UNKNOWN else {}),
        evidence=[f"synthetic evidence for {shot_type.value}"],
        rule_version=RULE_VERSION,
    )


def resolve(
    declared: ShotType | None, inferred: ShotTypeInference, contact: float = STRONG_CONTACT
) -> ShotTypeInference:
    return resolve_shot_type(
        declared, inferred, contact, min_contact_confidence=MIN_CONTACT_CONFIDENCE
    )


def test_declared_and_classifier_agree() -> None:
    inferred = classifier(ShotType.SERVE, 0.66)
    result = resolve(ShotType.SERVE, inferred)

    assert result.shot_type is ShotType.SERVE
    assert result.source is ShotTypeSource.USER_DECLARED
    assert result.confidence is None
    assert result.inferred_shot_type is ShotType.SERVE
    assert result.inferred_confidence == 0.66
    assert result.mismatch is False
    # The classifier's own output is kept verbatim as the cross-check.
    assert result.class_scores == inferred.class_scores
    assert result.evidence == inferred.evidence
    assert result.rule_version == inferred.rule_version


def test_declared_differs_from_a_confident_classifier_is_a_mismatch() -> None:
    result = resolve(ShotType.FOREHAND_TOPSPIN, classifier(ShotType.SERVE, 0.9))

    assert result.shot_type is ShotType.FOREHAND_TOPSPIN
    assert result.source is ShotTypeSource.USER_DECLARED
    assert result.confidence is None
    assert result.inferred_shot_type is ShotType.SERVE
    assert result.inferred_confidence == 0.9
    assert result.mismatch is True


def test_mismatch_fires_exactly_at_the_contact_threshold() -> None:
    result = resolve(ShotType.VOLLEY, classifier(ShotType.SERVE), MIN_CONTACT_CONFIDENCE)
    assert result.mismatch is True


def test_unknown_classifier_never_mismatches() -> None:
    result = resolve(ShotType.SERVE, classifier(ShotType.UNKNOWN, 0.41))

    assert result.shot_type is ShotType.SERVE
    assert result.inferred_shot_type is ShotType.UNKNOWN
    assert result.inferred_confidence == 0.41
    assert result.mismatch is False


def test_weak_contact_suppresses_the_mismatch() -> None:
    # A never-observed wrist once produced a confident bogus label; on weak
    # contact the classifier is not allowed to contradict the user.
    result = resolve(ShotType.SERVE, classifier(ShotType.VOLLEY, 1.0), WEAK_CONTACT)

    assert result.shot_type is ShotType.SERVE
    assert result.inferred_shot_type is ShotType.VOLLEY
    assert result.mismatch is False


@pytest.mark.parametrize("declared", [None, ShotType.UNKNOWN])
def test_no_declaration_is_todays_behaviour_marked_inferred(declared: ShotType | None) -> None:
    inferred = classifier(ShotType.BACKHAND_TWO_HANDED, 0.58)
    result = resolve(declared, inferred)

    assert result.source is ShotTypeSource.INFERRED
    assert result.inferred_shot_type is None
    assert result.inferred_confidence is None
    assert result.mismatch is False
    # Everything the classifier said is unchanged.
    for name in ("shot_type", "confidence", "class_scores", "evidence", "rule_version"):
        assert getattr(result, name) == getattr(inferred, name), name


@pytest.mark.parametrize("declared", [None, ShotType.UNKNOWN])
def test_no_declaration_never_mismatches_even_on_a_confident_read(
    declared: ShotType | None,
) -> None:
    assert resolve(declared, classifier(ShotType.SERVE, 1.0)).mismatch is False


@pytest.mark.parametrize("primary", sorted(UNBANDED_SHOT_TYPES))
def test_every_unbanded_primary_type_has_no_shot_specific_scoring(primary: ShotType) -> None:
    if primary is not ShotType.UNKNOWN:
        assert resolve(primary, classifier(ShotType.SERVE)).shot_specific_scoring is False
    assert resolve(None, classifier(primary)).shot_specific_scoring is False


@pytest.mark.parametrize("primary", [ShotType.FOREHAND_TOPSPIN, ShotType.FOREHAND_SLICE])
def test_both_forehands_have_shot_specific_scoring(primary: ShotType) -> None:
    assert primary in RUBRIC_V1
    assert resolve(primary, classifier(ShotType.SERVE)).shot_specific_scoring is True
    assert resolve(None, classifier(primary)).shot_specific_scoring is True


def test_shot_specific_scoring_follows_the_primary_not_the_classifier() -> None:
    # Classifier says forehand (banded); the user declared a serve (unbanded).
    assert resolve(ShotType.SERVE, classifier(ShotType.FOREHAND_TOPSPIN)).shot_specific_scoring is False
    assert resolve(ShotType.FOREHAND_SLICE, classifier(ShotType.SERVE)).shot_specific_scoring is True


def test_the_input_is_not_mutated() -> None:
    inferred = classifier(ShotType.SERVE)
    before = inferred.model_dump()
    resolve(ShotType.VOLLEY, inferred)
    assert inferred.model_dump() == before


def test_a_declared_type_cannot_carry_a_confidence() -> None:
    with pytest.raises(ValueError):
        ShotTypeInference(
            shot_type=ShotType.SERVE,
            confidence=0.5,
            class_scores={},
            evidence=[],
            source=ShotTypeSource.USER_DECLARED,
        )
