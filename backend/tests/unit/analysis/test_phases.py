"""Stage 12 unit tests. Synthetic data only; every answer known by construction.

The canonical fixture is documented in ``synthetic_swing``: a ready hold to
frame 7, a backward excursion bottoming at frame 15, and a logistic drive whose
speed peaks exactly at frame 25. Those three facts fix every phase boundary
analytically, so the assertions below are not re-derivations of the code under
test.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.analysis.metrics import phase_span
from app.analysis.phases import (
    CONTACT_HALF_WIDTH,
    MIN_PHASE_FRAMES,
    PHASE_ORDER,
    backward_extreme_frame,
    build_phase_list,
    enforce_minimum_lengths,
    first_reliable_frame_at_or_above,
    first_reliable_frame_below,
    monotonic_bounds,
    segment_swing_phases,
    tempo_ratio_from,
)
from app.models.enums import SwingPhaseName
from app.models.responses import SwingPhase

from tests.unit.analysis.synthetic_swing import (
    CONTACT_FRAME,
    FRAME_COUNT,
    RIGHT_HANDED,
    TAKEBACK_EXTREME_FRAME,
    UNKNOWN_HANDED,
    build_contact,
    build_sequence,
    hide_all,
    unusable_contact,
)

READY_EXIT_FRAME = 8


def linear_sequence(frame_count: int = FRAME_COUNT):
    """A constant-speed ramp: the hand is already moving at frame 0."""
    x = np.linspace(0.0, 2.0, frame_count)
    y = np.linspace(0.4, 1.0, frame_count)
    return build_sequence(frame_count=frame_count, wrist_xy=np.stack([x, y], axis=1))


# --- boundary-search helpers -------------------------------------------------


def test_first_reliable_frame_at_or_above_ignores_unreliable_crossings() -> None:
    speed = np.array([0.0, 9.0, 1.0, 5.0], dtype=np.float64)
    reliable = np.array([True, False, True, True])
    # Frame 1 crosses the threshold but was never observed; frame 3 is the
    # first reliable crossing.
    assert first_reliable_frame_at_or_above(speed, reliable, 4.0) == 3
    assert first_reliable_frame_at_or_above(speed, reliable, 1.0) == 2
    assert first_reliable_frame_at_or_above(speed, reliable, 99.0) is None
    assert first_reliable_frame_at_or_above(speed, reliable, 1.0, start=3) == 3


def test_backward_extreme_frame_is_the_minimum_of_forward_displacement() -> None:
    forward = np.array([0.5, 0.1, -0.9, -0.2, 0.7], dtype=np.float64)
    reliable = np.ones((5,), dtype=bool)
    assert backward_extreme_frame(forward, reliable, 0, 4) == 2
    # Masking the true minimum moves the answer to the best reliable frame.
    masked = reliable.copy()
    masked[2] = False
    assert backward_extreme_frame(forward, masked, 0, 4) == 3
    assert backward_extreme_frame(forward, reliable, 4, 0) is None


def test_first_reliable_frame_below_respects_the_window() -> None:
    speed = np.array([9.0, 9.0, 1.0, 9.0, 0.5], dtype=np.float64)
    reliable = np.ones((5,), dtype=bool)
    assert first_reliable_frame_below(speed, reliable, 2.0, 0, 4) == 2
    assert first_reliable_frame_below(speed, reliable, 2.0, 3, 4) == 4
    assert first_reliable_frame_below(speed, reliable, 0.1, 0, 4) is None


def test_monotonic_bounds_clamps_and_orders() -> None:
    assert monotonic_bounds([0, 5, 3, 9, 4], 10) == [0, 5, 5, 9, 9, 10]
    assert monotonic_bounds([0, -4, 2, 40, 41], 10) == [0, 0, 2, 10, 10, 10]


def test_enforce_minimum_lengths_collapses_short_phases_forward() -> None:
    bounds, collapsed = enforce_minimum_lengths([0, 1, 3, 6, 9, 12])
    # ready would be 1 frame long: it collapses and gives its frame to takeback.
    assert bounds == [0, 0, 3, 6, 9, 12]
    assert collapsed == [SwingPhaseName.READY]


def test_enforce_minimum_lengths_collapses_the_last_phase_backwards() -> None:
    bounds, collapsed = enforce_minimum_lengths([0, 2, 4, 6, 11, 12])
    assert bounds == [0, 2, 4, 6, 12, 12]
    assert collapsed == [SwingPhaseName.FOLLOW_THROUGH]


def test_build_phase_list_encodes_a_collapse_as_end_equals_start_minus_one() -> None:
    stamps = np.arange(6, dtype=np.float64) / 30.0
    phases = build_phase_list([0, 0, 2, 4, 6, 6], stamps)
    ready = phases[0]
    assert (ready.start_frame, ready.end_frame) == (0, -1)
    assert ready.duration_s == 0.0
    assert ready.start_time_s == ready.end_time_s


def test_tempo_ratio_from_is_none_not_zero_when_the_forward_swing_collapsed() -> None:
    def phase(name: SwingPhaseName, duration: float) -> SwingPhase:
        return SwingPhase(
            name=name,
            start_frame=0,
            end_frame=1,
            start_time_s=0.0,
            end_time_s=duration,
            duration_s=duration,
        )

    assert tempo_ratio_from(
        [phase(SwingPhaseName.TAKEBACK, 0.4), phase(SwingPhaseName.FORWARD_SWING, 0.2)]
    ) == pytest.approx(2.0)
    assert (
        tempo_ratio_from(
            [
                phase(SwingPhaseName.TAKEBACK, 0.4),
                phase(SwingPhaseName.FORWARD_SWING, 0.0),
            ]
        )
        is None
    )


# --- the canonical swing -----------------------------------------------------


def test_canonical_swing_segments_into_the_constructed_boundaries() -> None:
    phases, warnings = segment_swing_phases(build_sequence(), RIGHT_HANDED, build_contact())
    assert phases is not None
    spans = {phase.name: (phase.start_frame, phase.end_frame) for phase in phases.phases}
    assert spans == {
        SwingPhaseName.READY: (0, READY_EXIT_FRAME - 1),
        SwingPhaseName.TAKEBACK: (READY_EXIT_FRAME, TAKEBACK_EXTREME_FRAME),
        SwingPhaseName.FORWARD_SWING: (TAKEBACK_EXTREME_FRAME + 1, CONTACT_FRAME - 2),
        SwingPhaseName.CONTACT: (
            CONTACT_FRAME - CONTACT_HALF_WIDTH,
            CONTACT_FRAME + CONTACT_HALF_WIDTH,
        ),
        SwingPhaseName.FOLLOW_THROUGH: (
            CONTACT_FRAME + CONTACT_HALF_WIDTH + 1,
            FRAME_COUNT - 1,
        ),
    }
    assert [phase.name for phase in phases.phases] == list(PHASE_ORDER)
    assert not any("collapsed" in warning for warning in warnings)


def test_canonical_phases_are_ordered_contiguous_and_cover_the_clip() -> None:
    phases, _ = segment_swing_phases(build_sequence(), RIGHT_HANDED, build_contact())
    assert phases is not None
    items = phases.phases
    assert len(items) == 5
    assert items[0].start_frame == 0
    assert items[-1].end_frame == FRAME_COUNT - 1
    for previous, current in zip(items, items[1:]):
        assert current.start_frame == previous.end_frame + 1


def test_forward_swing_ends_two_frames_before_contact_so_phases_never_overlap() -> None:
    """PIPELINE.md:926 says C-1 and :927 starts contact at C-1 -- they overlap.

    Non-overlap is the structural invariant a consumer can rely on
    (``responses.py:166``); the exact forward-swing end frame is not. The
    contradiction is resolved toward C-2.
    """
    phases, _ = segment_swing_phases(build_sequence(), RIGHT_HANDED, build_contact())
    assert phases is not None
    spans = {phase.name: (phase.start_frame, phase.end_frame) for phase in phases.phases}
    assert spans[SwingPhaseName.FORWARD_SWING][1] == CONTACT_FRAME - 2
    assert spans[SwingPhaseName.CONTACT][0] == CONTACT_FRAME - 1


@pytest.mark.parametrize("frame_count", [12, 20, 32, 48])
@pytest.mark.parametrize("contact_offset", [-6, -2, 0, 3])
def test_invariants_hold_across_clip_lengths_and_contact_frames(
    frame_count: int, contact_offset: int
) -> None:
    """Ordering, contiguity and non-overlap are structural, not situational."""
    seq = build_sequence(frame_count=frame_count)
    contact_frame = int(np.clip(frame_count // 2 + contact_offset, 0, frame_count - 1))
    phases, warnings = segment_swing_phases(
        seq, RIGHT_HANDED, build_contact(frame_index=contact_frame)
    )
    if phases is None:
        assert warnings
        return
    items = phases.phases
    assert [phase.name for phase in items] == list(PHASE_ORDER)
    assert items[0].start_frame == 0
    for previous, current in zip(items, items[1:]):
        assert current.start_frame == previous.end_frame + 1
        assert current.start_frame >= previous.start_frame
    for phase in items:
        assert phase.end_frame >= phase.start_frame - 1  # never more than empty
        if phase.end_frame < phase.start_frame:
            assert phase.duration_s == 0.0


# --- degenerate phases -------------------------------------------------------


def test_contact_at_frame_one_collapses_the_leading_phases() -> None:
    phases, warnings = segment_swing_phases(
        linear_sequence(), RIGHT_HANDED, build_contact(frame_index=1)
    )
    assert phases is not None
    spans = {phase.name: phase for phase in phases.phases}
    for name in (
        SwingPhaseName.READY,
        SwingPhaseName.TAKEBACK,
        SwingPhaseName.FORWARD_SWING,
    ):
        phase = spans[name]
        assert phase.end_frame == phase.start_frame - 1, name
        assert phase.duration_s == 0.0
        assert phase.start_time_s == phase.end_time_s
        # The contract across the seam, not just the field values.
        assert phase_span(phases, name) is None
    assert phase_span(phases, SwingPhaseName.CONTACT) == (0, 2)
    assert any("collapsed" in warning for warning in warnings)
    assert phases.tempo_ratio is None


def test_a_collapse_is_never_encoded_as_start_equals_end() -> None:
    """Regression guard: ``start == end`` is a ONE-frame window to phase_span.

    ``knee_flexion_min_deg`` has no minimum-length guard, so it would report a
    single instant as "minimum flexion during the forward swing".
    """
    for contact_frame in range(0, 8):
        phases, _ = segment_swing_phases(
            linear_sequence(), RIGHT_HANDED, build_contact(frame_index=contact_frame)
        )
        if phases is None:
            continue
        for phase in phases.phases:
            if phase.duration_s == 0.0 and phase.end_frame >= phase.start_frame:
                pytest.fail(f"{phase.name} collapsed but encoded as start == end")
            span = phase_span(phases, phase.name)
            if span is not None:
                assert span[1] - span[0] + 1 >= MIN_PHASE_FRAMES


def test_tempo_ratio_is_none_rather_than_zero_when_the_forward_swing_collapses() -> None:
    phases, _ = segment_swing_phases(
        linear_sequence(), RIGHT_HANDED, build_contact(frame_index=1)
    )
    assert phases is not None
    assert phases.tempo_ratio is None
    assert phases.tempo_ratio != 0.0


# --- variable frame rate -----------------------------------------------------


def test_tempo_ratio_comes_from_pts_not_from_frame_counts() -> None:
    """30 -> 24 fps mid-clip (PIPELINE.md:165). The two answers differ."""
    stamps = np.concatenate(
        [np.arange(16) / 30.0, 16 / 30.0 + np.arange(1, FRAME_COUNT - 15) / 24.0]
    )
    seq = build_sequence(timestamps_s=stamps)
    phases, _ = segment_swing_phases(seq, RIGHT_HANDED, build_contact())
    assert phases is not None
    spans = {phase.name: phase for phase in phases.phases}
    takeback = spans[SwingPhaseName.TAKEBACK]
    forward = spans[SwingPhaseName.FORWARD_SWING]

    pts_ratio = (
        stamps[takeback.end_frame] - stamps[takeback.start_frame]
    ) / (stamps[forward.end_frame] - stamps[forward.start_frame])
    frame_ratio = (takeback.end_frame - takeback.start_frame) / (
        forward.end_frame - forward.start_frame
    )
    assert pts_ratio != pytest.approx(frame_ratio)  # the point of the test
    assert phases.tempo_ratio == pytest.approx(pts_ratio)


# --- the None paths ----------------------------------------------------------


def test_returns_none_when_the_clip_is_shorter_than_five_frames() -> None:
    phases, warnings = segment_swing_phases(
        build_sequence(frame_count=4), RIGHT_HANDED, build_contact(frame_index=2)
    )
    assert phases is None
    assert warnings


def test_returns_none_when_no_frame_is_reliable() -> None:
    phases, warnings = segment_swing_phases(
        hide_all(build_sequence()), RIGHT_HANDED, build_contact()
    )
    assert phases is None
    assert any("reliab" in warning or "observed" in warning for warning in warnings)


def test_returns_none_when_the_peak_speed_is_not_positive() -> None:
    seq = build_sequence()
    still = dataclasses.replace(seq, velocity=np.zeros_like(seq.velocity))
    phases, warnings = segment_swing_phases(still, RIGHT_HANDED, build_contact())
    assert phases is None
    assert warnings


def test_returns_none_on_the_stage_nine_sequence_unusable_flag() -> None:
    phases, warnings = segment_swing_phases(
        build_sequence(), RIGHT_HANDED, unusable_contact()
    )
    assert phases is None
    assert any("sequence_unusable" in warning for warning in warnings)


def test_returns_none_when_handedness_is_unknown() -> None:
    """Defect (10): the racket wrist must not silently become the right wrist."""
    phases, warnings = segment_swing_phases(
        build_sequence(), UNKNOWN_HANDED, build_contact()
    )
    assert phases is None
    assert any("handedness" in warning for warning in warnings)


@pytest.mark.parametrize("contact_frame", [-3, FRAME_COUNT, FRAME_COUNT + 10])
def test_returns_none_when_the_contact_frame_is_out_of_range(contact_frame: int) -> None:
    phases, warnings = segment_swing_phases(
        build_sequence(), RIGHT_HANDED, build_contact(frame_index=contact_frame)
    )
    assert phases is None
    assert warnings


# --- never raises ------------------------------------------------------------


def test_never_raises_on_degenerate_input() -> None:
    cases = [
        (build_sequence(frame_count=0), build_contact(frame_index=0)),
        (build_sequence(frame_count=1), build_contact(frame_index=0)),
        (
            dataclasses.replace(
                build_sequence(), points=np.full((FRAME_COUNT, 33, 2), np.nan)
            ),
            build_contact(),
        ),
        (
            dataclasses.replace(
                build_sequence(),
                velocity=np.full((FRAME_COUNT, 33, 2), np.nan),
            ),
            build_contact(),
        ),
        (hide_all(build_sequence(), value=0.0), build_contact()),
        (build_sequence(), build_contact(frame_index=-1)),
        (build_sequence(), build_contact(frame_index=FRAME_COUNT + 5)),
    ]
    for seq, contact in cases:
        phases, warnings = segment_swing_phases(seq, RIGHT_HANDED, contact)
        assert phases is None or len(phases.phases) == 5
        if phases is None:
            assert warnings


def test_internal_failure_is_named_in_the_warnings() -> None:
    """An internal crash must not read as an honest "cannot segment"."""
    seq = build_sequence()
    broken = dataclasses.replace(seq, points=np.zeros((FRAME_COUNT, 5, 2)))
    phases, warnings = segment_swing_phases(broken, RIGHT_HANDED, build_contact())
    assert phases is None
    assert any(warning.startswith("swing-phase segmentation failed") for warning in warnings)


def test_low_contact_confidence_travels_with_the_phases() -> None:
    phases, warnings = segment_swing_phases(
        build_sequence(), RIGHT_HANDED, build_contact(confidence=0.10)
    )
    assert phases is not None
    assert any("contact confidence" in warning for warning in warnings)
