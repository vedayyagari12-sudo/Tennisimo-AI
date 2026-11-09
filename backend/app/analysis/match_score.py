"""Tennis score state machine and event replay (design 6.7, brief G13, G14).

PURE: stdlib only, deterministic, independent of ball math. Replay is the
only way to compute a score: the event log is append-only, a correction is a
new event with ``supersedes``, and ``replay_score`` rebuilds every state from
the start.

Rules covered: 0/15/30/40/deuce/advantage, no-ad (at 40-40 the next point
wins); games to ``games_per_set`` by 2 with a tiebreak at
``tiebreak_at_games`` all; tiebreak to 7 by 2 (first server serves one point,
then two each); final set ``tiebreak``, ``match_tiebreak_10`` (to 10 by 2, in
place of the set, recorded as a 1-0 set) or ``advantage`` (no tiebreak);
server alternates each game, so the player who received first in a tiebreak
serves first next set; serve side deuce on an even point count; ends change
after odd-numbered games of a set (so at a set's end when its game total is
odd) and every 6 points of a tiebreak. Doubles is team level.

Decisions taken here:

* ``effective_events``: an event superseded by another takes no part; the
  survivor of a chain (e1 <- e2 <- e3) sits in the HEAD's slot, so a
  correction to point 5 recomputes everything after point 5. If several
  events supersede the same one, the latest sequence wins. An INFERRED event
  can never supersede a USER event (G11: user events always win).
* Points after the match is won do not change the state.
* A no-ad 40-40 displays as "Deuce".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal

from app.analysis.court_model import CourtEnd
from app.models.court import FinalSetRule, InferenceBasis, PlayerId, ScoreEventKind, ScoreSource

__all__ = [
    "SCORE_RULES_VERSION",
    "FinalSetRule",
    "PlayerId",
    "ScoreConfig",
    "ScoreEvent",
    "ScoreEventKind",
    "ScoreSource",
    "ScoreState",
    "ScoreTimelineEntry",
    "apply_point",
    "effective_events",
    "initial_state",
    "point_labels",
    "points_display",
    "replay_score",
]

SCORE_RULES_VERSION: Final[str] = "score_rules_v1"
TIEBREAK_POINTS: Final[int] = 7
MATCH_TIEBREAK_POINTS: Final[int] = 10
TIEBREAK_END_CHANGE_POINTS: Final[int] = 6
_POINT_NAMES: Final[tuple[str, ...]] = ("0", "15", "30", "40")

ServeSide = Literal["deuce", "ad"]


@dataclass(frozen=True)
class ScoreConfig:
    best_of_sets: Literal[1, 3, 5]
    ad_scoring: bool
    games_per_set: int = 6
    tiebreak_at_games: int = 6
    final_set: FinalSetRule = FinalSetRule.TIEBREAK
    first_server: PlayerId = PlayerId.A
    player_a_end_at_start: CourtEnd = CourtEnd.NEAR


@dataclass(frozen=True)
class ScoreEvent:
    event_id: str
    sequence: int
    kind: ScoreEventKind
    winner: PlayerId | None
    source: ScoreSource
    rally_id: str | None
    supersedes: str | None
    #: Brief G13.
    basis: InferenceBasis | None = None
    needs_review: bool = False


@dataclass(frozen=True)
class ScoreState:
    sets: tuple[tuple[int, int], ...]
    games: tuple[int, int]
    points: tuple[int, int]
    in_tiebreak: bool
    server: PlayerId
    serve_side: ServeSide
    player_a_end: CourtEnd
    match_winner: PlayerId | None
    unresolved_before: int
    #: Who served the current game's first point (the tiebreak's first server
    #: inside a tiebreak). Internal to rotation; not on the wire.
    game_server: PlayerId = PlayerId.A


@dataclass(frozen=True)
class ScoreTimelineEntry:
    event_id: str
    state: ScoreState
    points_display: str


def _other(player: PlayerId) -> PlayerId:
    return PlayerId.B if player is PlayerId.A else PlayerId.A


def _swap(end: CourtEnd) -> CourtEnd:
    return CourtEnd.FAR if end is CourtEnd.NEAR else CourtEnd.NEAR


def _index(player: PlayerId) -> int:
    return 0 if player is PlayerId.A else 1


def initial_state(config: ScoreConfig) -> ScoreState:
    state = ScoreState(
        sets=(),
        games=(0, 0),
        points=(0, 0),
        in_tiebreak=False,
        server=config.first_server,
        serve_side="deuce",
        player_a_end=config.player_a_end_at_start,
        match_winner=None,
        unresolved_before=0,
        game_server=config.first_server,
    )
    if _is_final_set(state, config) and config.final_set is FinalSetRule.MATCH_TIEBREAK_10:
        return replace(state, in_tiebreak=True)
    return state


def _sets_won(state: ScoreState) -> tuple[int, int]:
    a = sum(1 for x, y in state.sets if x > y)
    return a, len(state.sets) - a


def _is_final_set(state: ScoreState, config: ScoreConfig) -> bool:
    a, b = _sets_won(state)
    half = config.best_of_sets // 2
    return a == half and b == half


def _match_tiebreak(state: ScoreState, config: ScoreConfig) -> bool:
    return _is_final_set(state, config) and config.final_set is FinalSetRule.MATCH_TIEBREAK_10


def _tiebreak_server(first: PlayerId, points_played: int) -> PlayerId:
    """Point ``points_played`` (0-based) of a tiebreak: first serves one,
    then two each."""
    if points_played == 0:
        return first
    return _other(first) if ((points_played - 1) // 2) % 2 == 0 else first


def _side(points_played: int) -> ServeSide:
    return "deuce" if points_played % 2 == 0 else "ad"


def apply_point(state: ScoreState, winner: PlayerId, config: ScoreConfig) -> ScoreState:
    """The state after ``winner`` wins one point."""
    if state.match_winner is not None:
        return state
    points = list(state.points)
    points[_index(winner)] += 1
    a, b = points
    if state.in_tiebreak:
        target = MATCH_TIEBREAK_POINTS if _match_tiebreak(state, config) else TIEBREAK_POINTS
        played = a + b
        if max(a, b) >= target and abs(a - b) >= 2:
            # No 6-point change on the last point: the set-end change follows.
            if _match_tiebreak(state, config):
                return _finish_set(state, config, (1, 0) if a > b else (0, 1), _swap(state.player_a_end))
            games = list(state.games)
            games[_index(winner)] += 1
            return _finish_game(state, config, (games[0], games[1]), state.player_a_end, tiebreak_done=True)
        end = state.player_a_end
        if played % TIEBREAK_END_CHANGE_POINTS == 0:
            end = _swap(end)
        return replace(
            state,
            points=(a, b),
            server=_tiebreak_server(state.game_server, played),
            serve_side=_side(played),
            player_a_end=end,
        )
    won = (a >= 4 and a - b >= 2) or (b >= 4 and b - a >= 2)
    if not config.ad_scoring and (a >= 4 or b >= 4):
        won = True
    if not won:
        return replace(state, points=(a, b), serve_side=_side(a + b))
    games = list(state.games)
    games[_index(winner)] += 1
    return _finish_game(state, config, (games[0], games[1]), state.player_a_end, tiebreak_done=False)


def _finish_game(
    state: ScoreState,
    config: ScoreConfig,
    games: tuple[int, int],
    end: CourtEnd,
    *,
    tiebreak_done: bool,
) -> ScoreState:
    a, b = games
    final = _is_final_set(state, config)
    set_won = tiebreak_done or (
        max(a, b) >= config.games_per_set and abs(a - b) >= 2
    )
    if set_won:
        if (a + b) % 2 == 1:
            end = _swap(end)
        return _finish_set(state, config, games, end)
    if (a + b) % 2 == 1:
        end = _swap(end)
    next_server = _other(state.game_server)
    tiebreak = a == b == config.tiebreak_at_games and not (final and config.final_set is FinalSetRule.ADVANTAGE)
    return replace(
        state,
        games=games,
        points=(0, 0),
        in_tiebreak=tiebreak,
        server=next_server,
        serve_side="deuce",
        player_a_end=end,
        game_server=next_server,
    )


def _finish_set(state: ScoreState, config: ScoreConfig, games: tuple[int, int], end: CourtEnd) -> ScoreState:
    sets = (*state.sets, games)
    next_server = _other(state.game_server)
    done = replace(
        state,
        sets=sets,
        games=(0, 0),
        points=(0, 0),
        in_tiebreak=False,
        server=next_server,
        serve_side="deuce",
        player_a_end=end,
        game_server=next_server,
    )
    a, b = _sets_won(done)
    needed = config.best_of_sets // 2 + 1
    if a >= needed or b >= needed:
        return replace(done, match_winner=PlayerId.A if a > b else PlayerId.B)
    if _match_tiebreak(done, config):
        return replace(done, in_tiebreak=True)
    return done


def point_labels(state: ScoreState, config: ScoreConfig) -> tuple[str, str]:
    """Brief G14: (player_a, player_b) point labels."""
    a, b = state.points
    if state.in_tiebreak:
        return str(a), str(b)
    if a >= 3 and b >= 3:
        if a == b or not config.ad_scoring:
            return "40", "40"
        return ("Ad", "40") if a > b else ("40", "Ad")
    return _POINT_NAMES[min(a, 3)], _POINT_NAMES[min(b, 3)]


def points_display(state: ScoreState, config: ScoreConfig) -> str:
    """Player_a first: "30-15", "Deuce", "Ad-40", or "6-5" in a tiebreak."""
    a, b = state.points
    if not state.in_tiebreak and a >= 3 and b >= 3 and (a == b or not config.ad_scoring):
        return "Deuce"
    label_a, label_b = point_labels(state, config)
    return f"{label_a}-{label_b}"


def effective_events(events: Sequence[ScoreEvent]) -> tuple[ScoreEvent, ...]:
    """The surviving events, each in its chain head's slot, in slot order."""
    by_id = {e.event_id: e for e in events}
    # Valid supersedes edges: never an inferred event over a user one.
    replaced_by: dict[str, ScoreEvent] = {}
    for event in sorted(events, key=lambda e: (e.sequence, e.event_id)):
        target = by_id.get(event.supersedes) if event.supersedes is not None else None
        if target is None or target.event_id == event.event_id:
            continue
        if event.source is ScoreSource.INFERRED and target.source is ScoreSource.USER:
            continue
        replaced_by[target.event_id] = event  # the latest sequence wins

    def head_of(event: ScoreEvent) -> ScoreEvent:
        seen = {event.event_id}
        current = event
        while current.supersedes is not None:
            target = by_id.get(current.supersedes)
            if target is None or replaced_by.get(target.event_id) is not current or target.event_id in seen:
                break
            seen.add(target.event_id)
            current = target
        return current

    survivors = [e for e in events if e.event_id not in replaced_by and _is_chain_member(e, by_id, replaced_by)]
    slotted = sorted(survivors, key=lambda e: (head_of(e).sequence, e.sequence, e.event_id))
    return tuple(slotted)


def _is_chain_member(event: ScoreEvent, by_id: dict[str, ScoreEvent], replaced_by: dict[str, ScoreEvent]) -> bool:
    """False for a would-be correction whose supersedes was refused or lost
    to a later correction of the same event (it then stands for nothing)."""
    if event.supersedes is None or event.supersedes not in by_id:
        return True
    return replaced_by.get(event.supersedes) is event


def replay_score(config: ScoreConfig, events: Sequence[ScoreEvent]) -> tuple[ScoreTimelineEntry, ...]:
    """One timeline entry per effective event: the state after it."""
    state = initial_state(config)
    timeline: list[ScoreTimelineEntry] = []
    for event in effective_events(events):
        if event.kind is ScoreEventKind.POINT_WON and event.winner is not None:
            state = apply_point(state, event.winner, config)
        elif event.kind is ScoreEventKind.POINT_UNRESOLVED:
            state = replace(state, unresolved_before=state.unresolved_before + 1)
        timeline.append(ScoreTimelineEntry(event.event_id, state, points_display(state, config)))
    return tuple(timeline)
