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
from app.analysis.contact import (
    MAX_SUSTAINED_RUN_FRAMES,
    candidate_peak_indices,
    detect_contact_frame,
    racket_wrist_reliable,
    select_contact_index,
)
from app.analysis.metrics import compute_swing_metrics
from app.analysis.normalize import LEFT_WRIST, RIGHT_WRIST, VISIBILITY_THRESHOLD
from app.models.enums import Handedness, HandednessSource
from app.models.internal import NormalizedSequence
from app.models.responses import ContactDetection, HandednessResult, SwingMetrics

from tests.unit.analysis.synthetic import make_normalized_sequence
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


# --- Defect 2 (PIPELINE.md 7.1) ---------------------------------------------


def test_a_sequence_with_dead_ends_carries_the_validity_mask() -> None:
    """The Stage 12 fixture can now express real footage's shape.

    Before this, every synthetic NormalizedSequence was valid end to end, which
    is why 670 green tests said nothing about a Stage 7 gate that rejected a
    third of the real corpus. Frames outside the run are invalid AND their
    per-landmark visibility is below the gate, so a downstream stage that keys
    off either signal sees the same clip.
    """
    seq = build_sequence(frame_count=32, valid_run=(8, 28))

    assert not seq.valid[:8].any()
    assert seq.valid[8:28].all()
    assert not seq.valid[28:].any()
    assert np.all(seq.visibility[:8] < VISIBILITY_THRESHOLD)
    assert np.all(seq.visibility[8:28] >= VISIBILITY_THRESHOLD)
    # Contact at frame 25 is inside the run, so Stage 9's reliability view and
    # the Stage 7 mask agree about the frames that matter.
    assert seq.valid[CONTACT_FRAME]


# --- Defect 3 (PIPELINE.md 9.2) ---------------------------------------------


SERVE_FPS: float = 30.0
#: The wrist-speed proxy peaks during the explosive arm drive, ~0.6 s BEFORE the
#: racket head reaches the ball: the racket's final acceleration comes from
#: forearm pronation and wrist snap, rotation ABOUT the wrist, at a moment when
#: the wrist's own translational speed has already fallen. Measured on the
#: ground-truth clip: ~9-11 TU/s at the drive versus ~2.6-3.9 TU/s at contact.
SERVE_DRIVE_FRAME: int = 22
SERVE_CONTACT_FRAME: int = 40


def serve_shaped_sequence() -> tuple[NormalizedSequence, np.ndarray]:
    """A synthetic SERVE: the tallest wrist-speed peak is 0.6 s before contact.

    ``synthetic_swing.py`` produces a single peak that COINCIDES with contact,
    which is precisely why the unit suite was green against a defect visible on
    every serve (PIPELINE.md 9.2.7). Here the drive peak is taller and the arm
    is still folded at it; the contact peak is slower and at full extension.
    """
    frame_count = 70
    frames = np.arange(frame_count, dtype=np.float64)
    speed = (
        0.5
        + 10.0 * np.exp(-(((frames - SERVE_DRIVE_FRAME) / 3.0) ** 2))
        + 3.4 * np.exp(-(((frames - SERVE_CONTACT_FRAME) / 3.0) ** 2))
    )

    # Reach: folded through the drive, fully extended through contact.
    shoulder = np.array([0.2, 1.0])
    reach = 0.5 + 0.5 * np.exp(-(((frames - SERVE_CONTACT_FRAME) / 9.0) ** 2))
    theta = np.linspace(0.25, 1.15, frame_count)
    wrist = np.stack(
        [shoulder[0] + reach * np.sin(theta), shoulder[1] - reach * np.cos(theta)],
        axis=1,
    )
    return make_normalized_sequence(speed, fps=SERVE_FPS, wrist_positions=wrist), speed


def test_a_serve_does_not_return_the_arm_drive_as_contact() -> None:
    """The defect, pinned: three signals said "not a contact" and it was returned anyway.

    Before this fix Stage 9 took a single argmax, walked it to a contact index,
    and only THEN ran the gates -- so the implausible winner had nothing to lose
    to and the gates only made a wrong answer's confidence small.
    """
    seq, speed = serve_shaped_sequence()
    assert int(np.argmax(speed)) == SERVE_DRIVE_FRAME, "the fixture must be serve-shaped"
    assert speed[SERVE_DRIVE_FRAME] > speed[SERVE_CONTACT_FRAME]

    result = detect_contact_frame(seq, RIGHT_HANDED)

    assert result.peak_frame_index == SERVE_CONTACT_FRAME
    assert abs(result.frame_index - SERVE_CONTACT_FRAME) <= MAX_SUSTAINED_RUN_FRAMES
    assert "contact_not_found" not in result.sanity_flags
    # The drive frame is what the pre-fix code returned.
    assert abs(result.frame_index - SERVE_DRIVE_FRAME) > MAX_SUSTAINED_RUN_FRAMES


def test_the_arm_drive_candidate_is_rejected_before_selection_not_after() -> None:
    """The distinction that matters: a FILTER, not a discount."""
    seq, speed = serve_shaped_sequence()
    reliable = racket_wrist_reliable(seq, Handedness.RIGHT)

    candidates = candidate_peak_indices(speed, reliable, window_start=0)
    assert candidates[0] == SERVE_DRIVE_FRAME, "the wrong answer is still ranked first"
    assert SERVE_CONTACT_FRAME in candidates

    index, flags, audit = select_contact_index(
        seq, Handedness.RIGHT, speed, reliable, window_start=0
    )
    assert flags == []
    assert index is not None and abs(index - SERVE_CONTACT_FRAME) <= MAX_SUSTAINED_RUN_FRAMES
    # The audit trail names the rejection rather than leaving it invisible.
    assert audit[0][1] == ["arm_not_extended"]
