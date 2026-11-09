"""Build step 15: ``analysis/point_inference.py`` (design 6.8, brief G11, G13).

Done when: every section 6.8 row; too close and lost track give no winner; a
double fault needs two faults. Plus G11: the running state, the re-called
serve box, and user corrections that always win.

Player A serves first from the near end, so the first serve goes to the FAR
deuce box: x in [-4.115, 0.025], y in [0, 6.40] (brief G12).
"""

from __future__ import annotations

import numpy as np

from app.analysis.court_model import CourtEnd, CourtLine
from app.analysis.line_calls import LINE_CALLS_VERSION, BouncePoint, LineCall
from app.analysis.match_score import ScoreConfig, ScoreEvent, initial_state, replay_score
from app.analysis.placement import ShotLanding, classify_landing
from app.analysis.point_inference import (
    POINT_INFERENCE_VERSION,
    InferenceBasis,
    events_from_rallies,
    infer_point_outcome,
    serve_box_calls,
)
from app.analysis.rally import Rally
from app.models.court import (
    LengthConfidence,
    LineCallTarget,
    LineVerdict,
    PlayerId,
    RallyEndReason,
    ScoreEventKind,
    ScoreSource,
)

A, B = PlayerId.A, PlayerId.B
NEAR, FAR = CourtEnd.NEAR, CourtEnd.FAR
CONFIG = ScoreConfig(best_of_sets=3, ad_scoring=True)


def land(name: str, xy: tuple[float, float], verdict: LineVerdict | None, hitter: CourtEnd | None) -> ShotLanding:
    half = NEAR if xy[1] < 0 else FAR
    point = BouncePoint(name, 1.0, 0.004, xy, np.eye(2) * 1e-4, half, 0.9, ())
    call = None if verdict is None else LineCall(verdict, CourtLine.FAR_BASELINE, 0.3, 0.01, 0.02, (),
                                                 LINE_CALLS_VERSION, LineCallTarget.SINGLES_HALF)
    return classify_landing(point, call, hitter, None, hitter is not None, target=LineCallTarget.SINGLES_HALF)


def rally(name: str, start_s: float, landings: list[ShotLanding], reason: RallyEndReason,
          hits: int = 1, inferred: int = 0) -> Rally:
    length = hits + inferred
    return Rally(name, start_s, start_s + 3.0, length, length, length, LengthConfidence.HIGH, hits, inferred,
                 reason, tuple(landing.bounce.event_id for landing in landings), tuple(landings), ())


GOOD_SERVE = (-2.0, 4.0)  # far deuce box
LONG_SERVE = (-2.0, 8.0)  # past the far service line, still in the half


def three_shots(final: ShotLanding, reason: RallyEndReason) -> Rally:
    """A serves (near), B returns (far), A hits the last shot (near)."""
    return rally("r_0000", 1.0, [land("b0", GOOD_SERVE, LineVerdict.IN, NEAR),
                                 land("b1", (1.0, -7.0), LineVerdict.IN, FAR), final], reason, hits=3)


# ----------------------------------------------------------- 6.8 rows


def test_ball_out_goes_to_the_non_hitter() -> None:
    out = three_shots(land("b2", (0.0, 12.6), LineVerdict.OUT, NEAR), RallyEndReason.BALL_OUT)
    result = infer_point_outcome(out, initial_state(CONFIG))
    assert (result.winner, result.basis, result.needs_review) == (B, InferenceBasis.BALL_OUT, False)


def test_double_bounce_goes_to_the_last_hitter() -> None:
    first = land("b2", (0.0, 3.0), LineVerdict.IN, NEAR)
    second = land("b3", (0.0, 7.0), None, NEAR)
    two = rally("r_0000", 1.0, [land("b0", GOOD_SERVE, LineVerdict.IN, NEAR),
                                land("b1", (1.0, -7.0), LineVerdict.IN, FAR), first, second],
                RallyEndReason.DOUBLE_BOUNCE, hits=3)
    result = infer_point_outcome(two, initial_state(CONFIG))
    assert (result.winner, result.basis, result.needs_review) == (A, InferenceBasis.DOUBLE_BOUNCE, False)


def test_net_goes_to_the_non_hitter() -> None:
    own_half = three_shots(land("b2", (0.0, -1.0), LineVerdict.OUT, NEAR), RallyEndReason.NET)
    result = infer_point_outcome(own_half, initial_state(CONFIG))
    assert (result.winner, result.basis) == (B, InferenceBasis.NET)


def test_too_close_lost_track_and_unseen_give_no_winner() -> None:
    state = initial_state(CONFIG)
    close = three_shots(land("b2", (0.0, 11.9), LineVerdict.TOO_CLOSE_TO_CALL, NEAR), RallyEndReason.LOST_TRACK)
    lost = three_shots(land("b2", (0.0, 9.0), LineVerdict.IN, NEAR), RallyEndReason.LOST_TRACK)
    unseen = three_shots(land("b2", (0.0, 9.0), LineVerdict.IN, NEAR), RallyEndReason.TIMEOUT)
    uncalled = three_shots(land("b2", (0.0, 12.6), None, NEAR), RallyEndReason.LOST_TRACK)
    for case, basis in ((close, InferenceBasis.TOO_CLOSE), (lost, InferenceBasis.LOST_TRACK),
                        (unseen, InferenceBasis.UNSEEN_BOUNCE), (uncalled, InferenceBasis.TOO_CLOSE)):
        result = infer_point_outcome(case, state)
        assert (result.winner, result.basis, result.needs_review) == (None, basis, True)


def test_unknown_hitter_cannot_name_a_winner() -> None:
    out = rally("r_0000", 1.0, [land("b0", (0.0, 12.6), LineVerdict.OUT, None)], RallyEndReason.BALL_OUT,
                hits=0, inferred=2)
    result = infer_point_outcome(out, initial_state(CONFIG))
    assert (result.winner, result.basis, result.needs_review) == (None, InferenceBasis.BALL_OUT, True)


# ------------------------------------------------------------- serving


def serve_only(name: str, start_s: float, xy: tuple[float, float]) -> Rally:
    return rally(name, start_s, [land(f"b_{name}", xy, LineVerdict.IN, NEAR)], RallyEndReason.LOST_TRACK)


def test_first_fault_emits_nothing_and_a_double_fault_needs_two() -> None:
    fault_1, fault_2 = serve_only("r_0000", 1.0, LONG_SERVE), serve_only("r_0001", 6.0, LONG_SERVE)
    assert events_from_rallies([fault_1], CONFIG) == ()
    state = initial_state(CONFIG)
    first = infer_point_outcome(fault_1, state, serve_call=serve_box_calls([fault_1], CONFIG)["b_r_0000"])
    assert first.serve_fault and first.winner is None and first.basis is InferenceBasis.SERVE_FAULT
    (double,) = events_from_rallies([fault_1, fault_2], CONFIG)
    assert (double.kind, double.winner, double.basis, double.needs_review) == (
        ScoreEventKind.POINT_WON, B, InferenceBasis.DOUBLE_FAULT, False)
    assert (double.event_id, double.rally_id, double.source) == ("se_0000", "r_0001", ScoreSource.INFERRED)


def test_a_point_between_faults_resets_the_count() -> None:
    fault = serve_only("r_0000", 1.0, LONG_SERVE)
    point = three_shots(land("b2", (0.0, 12.6), LineVerdict.OUT, NEAR), RallyEndReason.BALL_OUT)
    point = Rally("r_0001", 6.0, 9.0, 3, 3, 3, LengthConfidence.HIGH, 3, 0, RallyEndReason.BALL_OUT,
                  point.event_ids, point.landings, ())
    second = serve_only("r_0002", 12.0, (2.0, 8.0))  # 0-15: the ad box, and long of it
    events = events_from_rallies([fault, point, second], CONFIG)
    assert [e.basis for e in events] == [InferenceBasis.BALL_OUT]  # the later fault is a first fault again


def test_serve_is_called_against_the_box_not_the_half() -> None:
    long_serve = serve_only("r_0000", 1.0, LONG_SERVE)
    assert long_serve.landings[0].call is not None and long_serve.landings[0].call.verdict is LineVerdict.IN
    calls = serve_box_calls([long_serve], CONFIG)
    assert calls["b_r_0000"].verdict is LineVerdict.OUT
    assert calls["b_r_0000"].deciding_line is CourtLine.FAR_SERVICE_LINE
    assert calls["b_r_0000"].target is LineCallTarget.SERVICE_BOX_DEUCE
    ace = serve_only("r_0001", 1.0, GOOD_SERVE)
    assert serve_box_calls([ace], CONFIG)["b_r_0001"].verdict is LineVerdict.IN
    (unresolved,) = events_from_rallies([ace], CONFIG)
    assert unresolved.kind is ScoreEventKind.POINT_UNRESOLVED and unresolved.needs_review


def test_uncalled_serve_needs_review() -> None:
    uncalled = rally("r_0000", 1.0, [land("b0", LONG_SERVE, None, NEAR)], RallyEndReason.LOST_TRACK)
    (event,) = events_from_rallies([uncalled], CONFIG)
    assert event.basis is InferenceBasis.TOO_CLOSE and event.needs_review
    assert serve_box_calls([uncalled], CONFIG) == {}


# ------------------------------------------------- G11: user events win


def test_a_user_correction_moves_the_next_serve_box() -> None:
    """Rally 0 is unresolved. Its correction (A won) makes 15-0, so rally 1's
    serve goes to the far AD box (x > 0): the same bounce is then in."""
    unresolved = three_shots(land("b2", (0.0, 9.0), LineVerdict.IN, NEAR), RallyEndReason.LOST_TRACK)
    to_ad_box = serve_only("r_0001", 6.0, (2.0, 4.0))
    (inferred,) = events_from_rallies([unresolved, to_ad_box], CONFIG)  # rally 1: a first fault
    assert inferred.basis is InferenceBasis.LOST_TRACK
    user = ScoreEvent("u1", 100, ScoreEventKind.POINT_WON, A, ScoreSource.USER, "r_0000", "se_0000")
    corrected = events_from_rallies([unresolved, to_ad_box], CONFIG, [user])
    assert [e.event_id for e in corrected] == ["se_0000", "se_0001"]
    assert corrected[1].rally_id == "r_0001" and corrected[1].kind is ScoreEventKind.POINT_UNRESOLVED
    calls = serve_box_calls([unresolved, to_ad_box], CONFIG, [user])
    assert calls["b_r_0001"].verdict is LineVerdict.IN and calls["b_r_0001"].target is LineCallTarget.SERVICE_BOX_AD
    final = replay_score(CONFIG, [*corrected, user])
    assert final[0].event_id == "u1" and final[0].state.points == (1, 0)


def test_version_constant() -> None:
    assert POINT_INFERENCE_VERSION == "point_inference_v1"
