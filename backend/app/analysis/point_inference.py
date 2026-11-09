"""Suggested point outcomes from rallies: an estimate with clear limits (6.8).

PURE: stdlib only. Inference never sets a score by itself: it emits
``ScoreEvent`` suggestions (source ``inferred``) that ``match_score`` replays
with the user's corrections, which always win (brief G11).

Rows of the 6.8 table, judged on the rally's last shot:

=========================================  ==========================  ===============
Observed                                   Suggestion                  Basis
=========================================  ==========================  ===============
Final bounce confidently out               point to the non-hitter     ``ball_out``
Second bounce on one half                  point to the last hitter    ``double_bounce``
Serve confidently out of the box, first    nothing; first-fault state  ``serve_fault``
Second consecutive serve fault             point to the receiver       ``double_fault``
Net contact, no later bounce beyond        point to the non-hitter     ``net``
Final bounce too close to call             none: needs review          ``too_close``
Track lost before an outcome               none: needs review          ``lost_track``
Far-side bounce unseen (rally timeout)     none: needs review          ``unseen_bounce``
=========================================  ==========================  ===============

Brief G11: ``events_from_rallies`` loops over rallies with a running state.
Each rally's first landing is the serve; it is re-called through
``call_region`` against ``service_box_region(receiver_end, serve_side)`` from
that state, so a corrected earlier point moves the box and the call.

Decisions taken here:

* A bounce with no call (calls not allowed, brief G21) cannot be judged in or
  out: it is treated as ``too_close`` (no confident verdict), needs review.
* A rally whose first landing has a known hitter who is NOT the server did
  not see its serve bounce: no fault logic runs and the last shot decides.
* A known outcome whose hitter's end is unknown (no pose, no bounce before
  it) cannot name a winner: same basis, no winner, needs review.
* A user event with no ``supersedes`` is a point of its own and joins the
  running state once its ``sequence`` is no later than the rally's index.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.analysis.court_model import CourtEnd, service_box_region
from app.analysis.line_calls import LineCall, call_region
from app.analysis.match_score import (
    ScoreConfig,
    ScoreEvent,
    ScoreState,
    initial_state,
    replay_score,
)
from app.analysis.placement import ShotLanding
from app.analysis.rally import Rally
from app.models.court import (
    InferenceBasis,
    LineCallTarget,
    LineVerdict,
    PlayerId,
    RallyEndReason,
    ScoreEventKind,
    ScoreSource,
)

__all__ = [
    "POINT_INFERENCE_VERSION",
    "InferenceBasis",
    "PointInference",
    "events_from_rallies",
    "infer_point_outcome",
    "serve_box_calls",
]

POINT_INFERENCE_VERSION: Final[str] = "point_inference_v1"


@dataclass(frozen=True)
class PointInference:
    winner: PlayerId | None
    basis: InferenceBasis
    needs_review: bool
    #: True only for a first serve fault: no point, the fault count rises.
    serve_fault: bool = False


def _swap(end: CourtEnd) -> CourtEnd:
    return CourtEnd.FAR if end is CourtEnd.NEAR else CourtEnd.NEAR


def _other(player: PlayerId) -> PlayerId:
    return PlayerId.B if player is PlayerId.A else PlayerId.A


def _player_at(end: CourtEnd, state: ScoreState) -> PlayerId:
    return PlayerId.A if end is state.player_a_end else PlayerId.B


def _end_of(player: PlayerId, state: ScoreState) -> CourtEnd:
    return state.player_a_end if player is PlayerId.A else _swap(state.player_a_end)


def _serve_landing(rally: Rally, state: ScoreState) -> ShotLanding | None:
    """The rally's first landing, if it can be the serve's bounce."""
    if not rally.landings:
        return None
    first = rally.landings[0]
    if first.hitter_end is not None and first.hitter_end is not _end_of(state.server, state):
        return None
    return first


def _serve_call(rally: Rally, state: ScoreState) -> LineCall | None:
    landing = _serve_landing(rally, state)
    if landing is None or landing.call is None:
        return None
    receiver_end = _swap(_end_of(state.server, state))
    target = LineCallTarget.SERVICE_BOX_DEUCE if state.serve_side == "deuce" else LineCallTarget.SERVICE_BOX_AD
    return call_region(landing.bounce, service_box_region(receiver_end, state.serve_side), target=target)


def _review(basis: InferenceBasis) -> PointInference:
    return PointInference(winner=None, basis=basis, needs_review=True)


def _final_landing(rally: Rally) -> ShotLanding | None:
    """The first bounce of the rally's last shot (where its point is decided)."""
    if not rally.landings:
        return None
    start = len(rally.landings) - 1
    while start > 0 and rally.landings[start - 1].hitter_end is rally.landings[-1].hitter_end:
        start -= 1
    return rally.landings[start]


def infer_point_outcome(
    rally: Rally,
    state: ScoreState,
    *,
    prior_faults: int = 0,
    serve_call: LineCall | None = None,
) -> PointInference:
    """One rally's suggestion. ``serve_call`` is the serve's box call (from
    ``events_from_rallies``); it decides only when the serve is the rally's
    last shot. ``prior_faults`` is 1 after a first-serve fault."""
    serve = _serve_landing(rally, state)
    if serve is not None and rally.length_min <= 1:  # the serve was the only shot
        if serve.call is None or serve_call is None:
            return _review(InferenceBasis.TOO_CLOSE)
        if serve_call.verdict is LineVerdict.OUT:
            if prior_faults >= 1:
                return PointInference(_other(state.server), InferenceBasis.DOUBLE_FAULT, needs_review=False)
            return PointInference(None, InferenceBasis.SERVE_FAULT, needs_review=False, serve_fault=True)
        if serve_call.verdict is LineVerdict.TOO_CLOSE_TO_CALL:
            return _review(InferenceBasis.TOO_CLOSE)

    final = _final_landing(rally)
    hitter_end = final.hitter_end if final is not None else None
    reason = rally.end_reason

    def to_non_hitter(basis: InferenceBasis) -> PointInference:
        if hitter_end is None:
            return _review(basis)
        return PointInference(_other(_player_at(hitter_end, state)), basis, needs_review=False)

    if reason is RallyEndReason.NET:
        return to_non_hitter(InferenceBasis.NET)
    if reason is RallyEndReason.TIMEOUT:
        return _review(InferenceBasis.UNSEEN_BOUNCE)
    if final is not None and final.call is None:
        return _review(InferenceBasis.TOO_CLOSE)
    if reason is RallyEndReason.BALL_OUT:
        return to_non_hitter(InferenceBasis.BALL_OUT)
    if reason is RallyEndReason.DOUBLE_BOUNCE:
        if hitter_end is None:
            return _review(InferenceBasis.DOUBLE_BOUNCE)
        return PointInference(_player_at(hitter_end, state), InferenceBasis.DOUBLE_BOUNCE, needs_review=False)
    if final is not None and final.call is not None and final.call.verdict is LineVerdict.TOO_CLOSE_TO_CALL:
        return _review(InferenceBasis.TOO_CLOSE)
    return _review(InferenceBasis.LOST_TRACK)


def _infer(
    rallies: Sequence[Rally], config: ScoreConfig, user_events: Sequence[ScoreEvent]
) -> tuple[tuple[ScoreEvent, ...], dict[str, LineCall]]:
    emitted: list[ScoreEvent] = []
    box_calls: dict[str, LineCall] = {}
    faults = 0
    ordered = sorted(rallies, key=lambda r: (r.start_s, r.rally_id))
    for index, rally in enumerate(ordered):
        emitted_ids = {e.event_id for e in emitted}
        applicable = [
            e
            for e in user_events
            if (e.supersedes is not None and e.supersedes in emitted_ids | {u.event_id for u in user_events})
            or (e.supersedes is None and e.sequence <= index)
        ]
        timeline = replay_score(config, [*emitted, *applicable])
        state = timeline[-1].state if timeline else initial_state(config)
        serve_call = _serve_call(rally, state)
        serve = _serve_landing(rally, state)
        if serve_call is not None and serve is not None:
            box_calls[serve.bounce.event_id] = serve_call
        outcome = infer_point_outcome(rally, state, prior_faults=faults, serve_call=serve_call)
        if outcome.serve_fault:
            faults = 1
            continue  # G11: the first fault emits nothing
        faults = 0
        sequence = len(emitted)
        emitted.append(
            ScoreEvent(
                event_id=f"se_{sequence:04d}",
                sequence=index,
                kind=ScoreEventKind.POINT_WON if outcome.winner is not None else ScoreEventKind.POINT_UNRESOLVED,
                winner=outcome.winner,
                source=ScoreSource.INFERRED,
                rally_id=rally.rally_id,
                supersedes=None,
                basis=outcome.basis,
                needs_review=outcome.needs_review,
            )
        )
    return tuple(emitted), box_calls


def events_from_rallies(
    rallies: Sequence[Rally], config: ScoreConfig, user_events: Sequence[ScoreEvent] = ()
) -> tuple[ScoreEvent, ...]:
    """Inferred score events (ids ``se_{index:04d}``, brief G9), with the
    running state corrected by ``user_events`` as the loop goes (G11). The
    caller replays ``[*inferred, *user_events]`` with ``replay_score``."""
    return _infer(rallies, config, user_events)[0]


def serve_box_calls(
    rallies: Sequence[Rally], config: ScoreConfig, user_events: Sequence[ScoreEvent] = ()
) -> Mapping[str, LineCall]:
    """Brief G11: each serve's box call, keyed by its bounce id."""
    return _infer(rallies, config, user_events)[1]
