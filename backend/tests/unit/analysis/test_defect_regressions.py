"""Regression tests for the two ring-fenced defects fixed alongside Stages 12/14/15.

Defect A -- ``racket_wrist_index`` silently mapped ``Handedness.UNKNOWN`` to the
RIGHT wrist (``handedness.py:171-173`` before the fix). Combined with Stage 8's
measured weakness that produced every racket-hand metric on the wrong arm, with
no flag. "Never a default" applies hardest to a measurement INPUT.

Defect B -- ``compute_swing_metrics`` swallowed every exception and returned a
bare ``SwingMetrics()`` with an unchanged ``warnings`` list
(``metrics.py:685-686`` before the fix), making "internal crash" byte-identical
to "clip legitimately unmeasurable".

Both tests are written to fail against the pre-fix code and pass after.
Synthetic data only; no I/O.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.analysis.handedness import (
    UnknownHandednessError,
    racket_wrist_index,
)
from app.analysis.contact import detect_contact_frame
from app.analysis.metrics import compute_swing_metrics
from app.analysis.normalize import LEFT_WRIST, RIGHT_WRIST
from app.models.enums import Handedness, HandednessSource
from app.models.internal import NormalizedSequence
from app.models.responses import ContactDetection, HandednessResult, SwingMetrics

from tests.unit.analysis.synthetic_swing import (
    CONTACT_FRAME,
    build_contact,
    build_sequence,
    hide_all,
)

INTERNAL_FAILURE_PREFIX = "swing metric computation failed internally"

RIGHT_HANDED = HandednessResult(
    handedness=Handedness.RIGHT, confidence=0.95, source=HandednessSource.DETECTED
)
UNKNOWN_HANDED = HandednessResult(
    handedness=Handedness.UNKNOWN, confidence=0.10, source=HandednessSource.DETECTED
)


# --- Defect A ---------------------------------------------------------------


def test_known_handedness_still_maps_to_its_own_wrist() -> None:
    """The fix must not disturb the two cases that were already correct."""
    assert racket_wrist_index(Handedness.RIGHT) == RIGHT_WRIST
    assert racket_wrist_index(Handedness.LEFT) == LEFT_WRIST


def test_unknown_handedness_never_resolves_to_the_right_wrist() -> None:
    """UNKNOWN is not a hand. It must not silently become the right wrist."""
    with pytest.raises(UnknownHandednessError):
        racket_wrist_index(Handedness.UNKNOWN)


def test_unknown_handedness_does_not_produce_right_handed_metrics() -> None:
    """Before the fix this returned the SAME numbers as an explicit RIGHT read.

    That is the whole defect: an undetermined hand produced confident
    right-arm measurements indistinguishable from a real right-handed read.
    """
    seq = build_sequence()
    contact = build_contact()

    right_metrics, _ = compute_swing_metrics(seq, RIGHT_HANDED, contact)
    unknown_metrics, unknown_warnings = compute_swing_metrics(seq, UNKNOWN_HANDED, contact)

    # The right-handed read really does measure the racket arm...
    assert right_metrics.elbow_angle_at_contact_deg is not None
    assert right_metrics.contact_height_ratio is not None
    # ...and the unknown read must not silently repeat it.
    assert unknown_metrics.elbow_angle_at_contact_deg is None
    assert unknown_metrics.contact_height_ratio is None
    assert unknown_metrics.contact_point_forward_tu is None
    assert unknown_metrics != right_metrics
    assert any(INTERNAL_FAILURE_PREFIX in warning for warning in unknown_warnings)


def test_unknown_handedness_degrades_contact_detection_instead_of_guessing() -> None:
    """Stage 9 cannot pick a racket wrist either; it degrades, it does not guess."""
    seq = build_sequence()
    detection = detect_contact_frame(seq, UNKNOWN_HANDED)
    assert detection.confidence == 0.0
    assert detection.sanity_flags == ["sequence_unusable"]
    assert detection.peak_hand_speed_tu_s is None


# --- Defect B ---------------------------------------------------------------


def _broken_sequence(seq: NormalizedSequence) -> NormalizedSequence:
    """A sequence whose landmark axis is too short to hold a wrist index.

    Indexing landmark 16 out of a 5-landmark array raises ``IndexError`` deep
    inside Stage 13 -- an internal failure, not an unmeasurable clip.
    """
    count = int(seq.points.shape[0])
    return dataclasses.replace(
        seq,
        points=np.zeros((count, 5, 2), dtype=np.float64),
        velocity=np.zeros((count, 5, 2), dtype=np.float64),
        acceleration=np.zeros((count, 5, 2), dtype=np.float64),
        visibility=np.full((count, 5), 0.9, dtype=np.float64),
    )


def test_internal_failure_is_named_in_warnings() -> None:
    metrics, warnings = compute_swing_metrics(
        _broken_sequence(build_sequence()), RIGHT_HANDED, build_contact()
    )
    assert metrics == SwingMetrics()  # the no-raise return contract is unchanged
    assert any(warning.startswith(INTERNAL_FAILURE_PREFIX) for warning in warnings)


def test_unmeasurable_clip_is_distinguishable_from_an_internal_failure() -> None:
    """The point of the fix: two all-None results, two different explanations."""
    occluded = hide_all(build_sequence())
    contact = ContactDetection(
        frame_index=CONTACT_FRAME,
        time_s=0.0,
        contact_absolute_time_s=0.0,
        confidence=0.4,
        peak_hand_speed_tu_s=None,
        peak_frame_index=CONTACT_FRAME,
        prominence_ratio=1.0,
    )
    honest_metrics, honest_warnings = compute_swing_metrics(occluded, RIGHT_HANDED, contact)
    crashed_metrics, crashed_warnings = compute_swing_metrics(
        _broken_sequence(build_sequence()), RIGHT_HANDED, contact
    )

    assert honest_metrics == SwingMetrics()
    assert crashed_metrics == SwingMetrics()
    assert not any(w.startswith(INTERNAL_FAILURE_PREFIX) for w in honest_warnings)
    assert any(w.startswith(INTERNAL_FAILURE_PREFIX) for w in crashed_warnings)
