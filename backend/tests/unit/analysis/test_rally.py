"""Build step 13: ``analysis/rally.py`` (design 6.6, brief G10 and G15).

Done when: the length definition is tested for a serve fault (1), a let (1)
and a rally (k); the range contains the truth with gaps; quiet splits; a
two-ball scene is flagged. Events, landings and flights are hand-built, so
every count is exact.
"""

from __future__ import annotations

import numpy as np

from app.analysis.ball_events import BallEvent
from app.analysis.court_model import CourtEnd, CourtLine
from app.analysis.flight_track import BallFlight
from app.analysis.line_calls import LINE_CALLS_VERSION, BouncePoint, LineCall
from app.analysis.placement import ShotLanding, classify_landing
from app.analysis.rally import (
    LOST_TRACK_GAP_FLAG,
    Rally,
    TWO_BALLS_FLAG,
    LengthConfidence,
    RallyEndReason,
    group_event_runs,
    preceding_hits,
    rally_length,
    segment_rallies,
)
from app.models.court import BallEventKind, LineCallTarget, LineVerdict, RallyResponse

H, B, N = BallEventKind.HIT, BallEventKind.BOUNCE, BallEventKind.NET_CONTACT
NEAR, FAR = CourtEnd.NEAR, CourtEnd.FAR


def event(name: str, kind: BallEventKind, time_s: float, end: CourtEnd | None = None) -> BallEvent:
    return BallEvent(name, 0, kind, time_s, 0.004, (900.0, 500.0), np.eye(2), (0.0, 300.0), (0.0, -300.0),
                     0.9, (), end)


def land(bounce: BallEvent, half: CourtEnd, verdict: LineVerdict | None = LineVerdict.IN,
         hitter: CourtEnd | None = None) -> ShotLanding:
    y = 6.0 if half is FAR else -6.0
    point = BouncePoint(bounce.event_id, bounce.time_s, 0.004, (0.0, y), np.eye(2) * 0.01, half, 0.9, ())
    call = None if verdict is None else LineCall(verdict, CourtLine.FAR_BASELINE, 0.3, 0.1, 0.2, (),
                                                 LINE_CALLS_VERSION, LineCallTarget.SINGLES_HALF)
    return classify_landing(point, call, hitter, None, False, target=LineCallTarget.SINGLES_HALF)


def flight(start_s: float, end_s: float, flight_id: int = 0, fps: float = 60.0) -> BallFlight:
    times = np.arange(round(start_s * fps), round(end_s * fps) + 1) / fps
    return BallFlight(flight_id, times, np.zeros((len(times), 2)), np.ones(len(times), dtype=bool),
                      np.full(len(times), 2.0), 1.0)


def only(events: list[BallEvent], landings: list[ShotLanding], flights: list[BallFlight]) -> Rally:
    (rally,) = segment_rallies(events, landings, flights=flights)
    return rally


# ------------------------------------------------------- length definition


def test_serve_fault_is_length_one() -> None:
    first, second = event("b1", B, 1.6), event("b2", B, 2.3)
    landings = [land(first, FAR, LineVerdict.OUT), land(second, FAR, None)]
    rally = only([first, second], landings, [flight(1.0, 2.8)])
    assert (rally.length_hits, rally.length_min, rally.length_max) == (1, 1, 1)
    assert (rally.hits_observed, rally.hits_inferred) == (0, 1)  # the serve, inferred from the launch
    assert rally.end_reason is RallyEndReason.BALL_OUT  # an out ball bouncing on stays out
    assert rally.confidence is LengthConfidence.HIGH
    with_pose = only([event("h0", H, 1.0, NEAR), first, second], landings, [flight(1.0, 2.8)])
    assert (with_pose.length_hits, with_pose.hits_observed, with_pose.hits_inferred) == (1, 1, 0)


def test_let_is_length_one() -> None:
    cord, bounce = event("n1", N, 1.4), event("b1", B, 1.7)
    rally = only([cord, bounce], [land(bounce, FAR)], [flight(1.0, 2.5)])
    assert rally.length_hits == 1
    assert rally.end_reason is RallyEndReason.NET


def test_rally_of_k_counts_observed_and_inferred_hits() -> None:
    """serve (pose), far hit, near hit MISSED, far hit, out: 4 hits."""
    events = [
        event("h0", H, 1.0, NEAR), event("b1", B, 1.6), event("h1", H, 2.0, FAR), event("b2", B, 2.8),
        event("b3", B, 4.2), event("h3", H, 4.6, FAR), event("b4", B, 5.4),
    ]
    by_name = {e.event_id: e for e in events}
    landings = [
        land(by_name["b1"], FAR, hitter=NEAR), land(by_name["b2"], NEAR, hitter=FAR),
        land(by_name["b3"], FAR, hitter=FAR), land(by_name["b4"], NEAR, LineVerdict.OUT, hitter=FAR),
    ]
    rally = only(events, landings, [flight(1.0, 6.0)])
    assert (rally.hits_observed, rally.hits_inferred) == (3, 1)
    assert (rally.length_min, rally.length_hits, rally.length_max) == (4, 4, 4)
    assert rally.end_reason is RallyEndReason.BALL_OUT
    assert rally.event_ids == tuple(e.event_id for e in events)
    assert [landing.bounce.event_id for landing in rally.landings] == ["b1", "b2", "b3", "b4"]
    assert rally_length(events, landings, [flight(1.0, 6.0)]) == (4, 4, 4, LengthConfidence.HIGH)


# ------------------------------------------------------------ range, gaps


def test_range_contains_truth_with_lost_track_gaps() -> None:
    """Truth: 3 hits. The far return (hit 2) is lost in a 0.4 s gap AND the
    bounce after it is missed, so nothing observed shows it."""
    events = [event("h0", H, 1.0, NEAR), event("b1", B, 1.6), event("h2", H, 3.0, NEAR), event("b3", B, 3.6)]
    by_name = {e.event_id: e for e in events}
    landings = [land(by_name["b1"], FAR, hitter=NEAR), land(by_name["b3"], FAR, LineVerdict.OUT, hitter=NEAR)]
    one_gap = [flight(1.0, 1.9, 0), flight(2.3, 4.0, 1)]
    rally = only(events, landings, one_gap)
    assert (rally.length_min, rally.length_max) == (2, 3)
    assert rally.length_min <= 3 <= rally.length_max
    assert rally.confidence is LengthConfidence.MEDIUM and LOST_TRACK_GAP_FLAG in rally.flags
    two_gaps = [flight(1.0, 1.9, 0), flight(2.3, 2.6, 1), flight(2.9, 4.0, 2)]
    wider = only(events, landings, two_gaps)
    assert wider.length_max == 4 and wider.confidence is LengthConfidence.LOW
    # A join with an event at it is not a gap; nor is one of 0.2 s or less.
    joined = only(events, landings, [flight(1.0, 2.95, 0), flight(3.05, 4.0, 1)])
    assert joined.length_max == joined.length_min


# ---------------------------------------------------------------- quiet


def test_quiet_splits_rallies_but_a_long_flight_does_not() -> None:
    events = [event("h0", H, 1.0, NEAR), event("b1", B, 1.6), event("h1", H, 6.0, NEAR), event("b2", B, 6.6)]
    by_name = {e.event_id: e for e in events}
    landings = [land(by_name["b1"], FAR, hitter=NEAR), land(by_name["b2"], FAR, hitter=NEAR)]
    split = segment_rallies(events, landings, flights=[flight(1.0, 2.0, 0), flight(5.9, 7.0, 1)])
    assert [r.rally_id for r in split] == ["r_0000", "r_0001"]
    assert [r.event_ids for r in split] == [("h0", "b1"), ("h1", "b2")]
    assert split[0].start_s == 1.0 and split[0].end_s == 2.0
    carried = segment_rallies(events, landings, flights=[flight(1.0, 4.0, 0), flight(5.9, 7.0, 1)])
    assert len(carried) == 1
    assert len(group_event_runs(events)) == 2
    assert len(group_event_runs(events, flights=[flight(1.0, 4.0)])) == 1
    assert len(group_event_runs(events, max_quiet_s=5.0)) == 1


def test_activity_without_a_ball_contact_is_not_a_rally() -> None:
    kink = event("k1", BallEventKind.UNKNOWN_KINK, 1.5)
    assert segment_rallies([kink], [], flights=[flight(1.0, 2.0)]) == ()
    assert segment_rallies([], [], flights=[flight(1.0, 2.0)]) == ()


# ------------------------------------------------------------- two balls


def test_two_balls_in_one_run_are_flagged() -> None:
    events = [event("h0", H, 1.0, NEAR), event("b1", B, 1.6)]
    landings = [land(events[1], FAR, hitter=NEAR)]
    flagged = only(events, landings, [flight(1.0, 2.0, 0), flight(1.5, 2.6, 1)])
    assert TWO_BALLS_FLAG in flagged.flags
    single = only(events, landings, [flight(1.0, 2.0, 0)])
    assert TWO_BALLS_FLAG not in single.flags


# ------------------------------------------------------------ end reasons


def test_end_reasons() -> None:
    serve = event("h0", H, 1.0, NEAR)
    b1, b2 = event("b1", B, 1.6), event("b2", B, 2.2)
    double = only([serve, b1, b2], [land(b1, FAR, hitter=NEAR), land(b2, FAR, None, hitter=NEAR)], [flight(1.0, 2.5)])
    assert double.end_reason is RallyEndReason.DOUBLE_BOUNCE
    own_half = only([serve, b1], [land(b1, NEAR, hitter=NEAR)], [flight(1.0, 2.0)])
    assert own_half.end_reason is RallyEndReason.NET
    net = only([serve, event("n1", N, 1.4)], [], [flight(1.0, 1.8)])
    assert net.end_reason is RallyEndReason.NET
    unseen = only([serve, b1, event("h1", H, 2.0, FAR)], [land(b1, FAR, hitter=NEAR)], [flight(1.0, 2.6)])
    assert unseen.end_reason is RallyEndReason.TIMEOUT
    in_then_nothing = only([serve, b1], [land(b1, FAR, hitter=NEAR)], [flight(1.0, 2.0)])
    assert in_then_nothing.end_reason is RallyEndReason.LOST_TRACK
    too_close = only([serve, b1], [land(b1, FAR, LineVerdict.TOO_CLOSE_TO_CALL, hitter=NEAR)], [flight(1.0, 2.0)])
    assert too_close.end_reason is RallyEndReason.LOST_TRACK


# ---------------------------------------------------------------- G10


def test_preceding_hits_give_bounce_hitters() -> None:
    run = [event("b0", B, 0.5), event("h0", H, 1.0, NEAR), event("b1", B, 1.6), event("b2", B, 2.0),
           event("h1", H, 2.4, FAR), event("b3", B, 3.0)]
    hitters = preceding_hits(run)
    assert hitters["b0"] is None
    assert hitters["b1"] is run[1] and hitters["b2"] is run[1]
    assert hitters["b3"] is run[4]
    assert set(hitters) == {"b0", "b1", "b2", "b3"}


def test_rallies_satisfy_the_wire_invariants() -> None:
    events = [event("h0", H, 1.0, NEAR), event("b1", B, 1.6), event("h2", H, 3.0, NEAR), event("b3", B, 3.6)]
    landings = [land(events[1], FAR, hitter=NEAR), land(events[3], FAR, LineVerdict.OUT, hitter=NEAR)]
    rally = only(events, landings, [flight(1.0, 1.9, 0), flight(2.3, 4.0, 1)])
    RallyResponse(
        rally_id=rally.rally_id, start_s=rally.start_s, end_s=rally.end_s, length_hits=rally.length_hits,
        length_min=rally.length_min, length_max=rally.length_max, length_confidence=rally.confidence,
        hits_observed=rally.hits_observed, hits_inferred=rally.hits_inferred, end_reason=rally.end_reason,
        bounce_ids=[landing.bounce.event_id for landing in rally.landings], flags=list(rally.flags),
    )
