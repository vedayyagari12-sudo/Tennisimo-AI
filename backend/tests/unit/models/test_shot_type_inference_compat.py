"""Backward compatibility of ``ShotTypeInference`` with payloads stored before
the user-declared shot type existed.

The poll route returns the stored JSONB verbatim, but anything that validates a
stored payload (tests, tooling, a future read path) must keep accepting old rows.
"""

from __future__ import annotations

from typing import Any

from app.models.enums import ShotType, ShotTypeSource
from app.models.responses import ShotTypeInference

#: The exact ``shot_type`` object a v4 row carried before this change.
OLD_STORED: dict[str, Any] = {
    "shot_type": "serve",
    "confidence": 0.67,
    "class_scores": {
        "forehand_topspin": 0.0,
        "forehand_slice": 0.0,
        "backhand_one_handed": 0.11,
        "backhand_two_handed": 0.0,
        "serve": 0.67,
        "volley": 0.22,
    },
    "evidence": ["contact height ratio 1.62 > 1.30 threshold -> serve"],
    "rule_version": "shot_rules_v1",
}

NEW_KEYS: frozenset[str] = frozenset(
    {"source", "inferred_shot_type", "inferred_confidence", "mismatch", "shot_specific_scoring"}
)


def test_an_old_payload_without_the_new_keys_validates_with_safe_defaults() -> None:
    model = ShotTypeInference.model_validate(OLD_STORED)

    assert model.shot_type is ShotType.SERVE
    assert model.confidence == 0.67
    assert model.source is ShotTypeSource.INFERRED
    assert model.inferred_shot_type is None
    assert model.inferred_confidence is None
    assert model.mismatch is False
    # Not recorded on old rows: None, NOT True. True would claim an unbanded
    # serve had shot-specific scoring.
    assert model.shot_specific_scoring is None


def test_an_old_payload_round_trips() -> None:
    model = ShotTypeInference.model_validate(OLD_STORED)
    dumped = model.model_dump(mode="json")

    # Every old key comes back unchanged...
    for key, value in OLD_STORED.items():
        assert dumped[key] == value, key
    # ...the only additions are the new keys at their defaults...
    assert set(dumped) - set(OLD_STORED) == NEW_KEYS
    # ...and a second pass is a fixed point.
    assert ShotTypeInference.model_validate(dumped).model_dump(mode="json") == dumped


def test_a_declared_payload_with_null_confidence_round_trips() -> None:
    declared = {
        **OLD_STORED,
        "confidence": None,
        "source": "user_declared",
        "inferred_shot_type": "volley",
        "inferred_confidence": 0.67,
        "mismatch": True,
        "shot_specific_scoring": False,
    }
    model = ShotTypeInference.model_validate(declared)
    assert model.confidence is None
    assert model.model_dump(mode="json") == declared
