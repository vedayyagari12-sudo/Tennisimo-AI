"""Build step 14: ``analysis/match_score.py`` (design 6.7, brief G13, G14).

Done when: deuce and advantage; no-ad; 6-6 tiebreak; 7-6; server rotation in
the tiebreak; next-set server; ends; final-set rules; unresolved does not
advance; a correction anywhere equals a clean replay; append-only; a
parameterised full-match table. (No Dart or I/O: the purity test covers it.)

Points are written as strings of "A" / "B" (the point winner), "U" for an
unresolved point and "V" for a void one.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

from app.analysis.court_model import CourtEnd
from app.analysis.match_score import (
    SCORE_RULES_VERSION,
    FinalSetRule,
    PlayerId,
    ScoreConfig,
    ScoreEvent,
    ScoreEventKind,
    ScoreSource,
    ScoreState,
    apply_point,
    effective_events,
    initial_state,
    point_labels,
    points_display,
    replay_score,
)

A, B = PlayerId.A, PlayerId.B
NEAR, FAR = CourtEnd.NEAR, CourtEnd.FAR
BEST_OF_3 = ScoreConfig(best_of_sets=3, ad_scoring=True)


def events_for(points: str, *, source: ScoreSource = ScoreSource.INFERRED, start: int = 0) -> list[ScoreEvent]:
    out: list[ScoreEvent] = []
    for offset, code in enumerate(points):
        kind = {"A": ScoreEventKind.POINT_WON, "B": ScoreEventKind.POINT_WON,
                "U": ScoreEventKind.POINT_UNRESOLVED, "V": ScoreEventKind.POINT_VOID}[code]
        winner = {"A": A, "B": B}.get(code)
        index = start + offset
        out.append(ScoreEvent(f"se_{index:04d}", index, kind, winner, source, f"r_{index:04d}", None))
    return out


def play(points: str, config: ScoreConfig = BEST_OF_3) -> ScoreState:
    timeline = replay_score(config, events_for(points))
    return timeline[-1].state if timeline else initial_state(config)


def game(winner: str) -> str:
    return winner * 4


def games(sequence: str) -> str:
    return "".join(game(w) for w in sequence)


SIX_ALL = games("AB" * 6)  # A and B hold alternately: 6-6


# ----------------------------------------------------------------- points


def test_deuce_and_advantage() -> None:
    displays = [t.points_display for t in replay_score(BEST_OF_3, events_for("AAABBBABBB"))]
    assert displays == ["15-0", "30-0", "40-0", "40-15", "40-30", "Deuce", "Ad-40", "Deuce", "40-Ad", "0-0"]
    state = play("AAABBBABBB")
    assert state.games == (0, 1) and state.points == (0, 0)
    assert point_labels(play("AAABBBA"), BEST_OF_3) == ("Ad", "40")


def test_no_ad_deciding_point() -> None:
    config = ScoreConfig(best_of_sets=3, ad_scoring=False)
    deuce = play("AAABBB", config)
    assert points_display(deuce, config) == "Deuce" and point_labels(deuce, config) == ("40", "40")
    assert play("AAABBBB", config).games == (0, 1)
    assert play("AAABBBA", config).games == (1, 0)


def test_serve_side_alternates_within_a_game() -> None:
    sides = [t.state.serve_side for t in replay_score(BEST_OF_3, events_for("ABAB"))]
    assert sides == ["ad", "deuce", "ad", "deuce"]
    assert initial_state(BEST_OF_3).serve_side == "deuce"


# --------------------------------------------------------------- tiebreak


def test_six_all_is_a_tiebreak_won_seven_six() -> None:
    state = play(SIX_ALL)
    assert state.games == (6, 6) and state.in_tiebreak
    assert points_display(play(SIX_ALL + "AAB"), BEST_OF_3) == "2-1"
    won = play(SIX_ALL + "A" * 7)
    assert won.sets == ((7, 6),) and won.games == (0, 0) and not won.in_tiebreak
    by_two = play(SIX_ALL + "AB" * 6 + "AA")
    assert by_two.sets == ((7, 6),)
    still = play(SIX_ALL + "AB" * 6 + "A")
    assert still.in_tiebreak and still.points == (7, 6)


def test_server_rotation_in_the_tiebreak_and_next_set() -> None:
    # 12 games alternate servers, so A serves game 13: the tiebreak.
    entering = play(SIX_ALL)
    assert entering.server is A
    servers = [entering.server] + [t.state.server for t in replay_score(BEST_OF_3, events_for(SIX_ALL + "AB" * 5))][48:]
    assert servers[:10] == [A, B, B, A, A, B, B, A, A, B]
    after = play(SIX_ALL + "A" * 7)
    assert after.server is B  # B received first in the tiebreak, so serves first next set


def test_server_alternates_each_game() -> None:
    servers = [t.state.server for t in replay_score(BEST_OF_3, events_for(games("AAB")))]
    assert servers[3] is B and servers[7] is A and servers[11] is B
    assert play("", ScoreConfig(best_of_sets=1, ad_scoring=True, first_server=B)).server is B


# ------------------------------------------------------------------- ends


def test_ends_change_after_odd_games_and_tiebreak_sixes() -> None:
    timeline = replay_score(BEST_OF_3, events_for(games("AAA")))
    ends_after_games = [timeline[4 * k + 3].state.player_a_end for k in range(3)]
    assert ends_after_games == [FAR, FAR, NEAR]
    tiebreak = replay_score(BEST_OF_3, events_for(SIX_ALL + "AB" * 6))
    assert play(SIX_ALL).player_a_end is NEAR  # 12 games: changed 6 times
    tb_ends = [t.state.player_a_end for t in tiebreak[48:]]
    assert tb_ends[4] is NEAR and tb_ends[5] is FAR and tb_ends[11] is NEAR
    # 7-6 by 7-0: 6 changes in the games (NEAR), one at 6 tiebreak points
    # (FAR), one at the set's end since 13 is odd (NEAR).
    assert play(SIX_ALL + "A" * 6).player_a_end is FAR
    assert play(SIX_ALL + "A" * 7).player_a_end is NEAR
    # 6-0: changes after games 1, 3, 5 (FAR), none at the set's end since 6
    # is even, then after game 1 of the next set (NEAR).
    assert play(games("AAAAA")).player_a_end is FAR
    assert play(games("AAAAAA")).player_a_end is FAR
    assert play(games("AAAAAA") + game("A")).player_a_end is NEAR
    # 7-5 ends on the tiebreak's 12th point: no 6-point change then, only the
    # set-end one: NEAR, FAR at 6 points, NEAR at the set's end.
    assert play(SIX_ALL + "AB" * 5 + "AA").player_a_end is NEAR


# ---------------------------------------------------------------- final set


@pytest.mark.parametrize(
    ("rule", "points", "final_set", "winner"),
    [
        (FinalSetRule.TIEBREAK, SIX_ALL + "B" * 7, (6, 7), B),
        (FinalSetRule.ADVANTAGE, SIX_ALL + games("AB") + games("BB"), (7, 9), B),
        (FinalSetRule.MATCH_TIEBREAK_10, "A" * 9 + "B" * 9 + "AA", (1, 0), A),
        (FinalSetRule.MATCH_TIEBREAK_10, "A" * 9 + "BBBBBBBBBB" + "AAA", (1, 0), A),
    ],
)
def test_final_set_rules(rule: FinalSetRule, points: str, final_set: tuple[int, int], winner: PlayerId) -> None:
    config = ScoreConfig(best_of_sets=3, ad_scoring=True, final_set=rule)
    one_all = games("AAAAAA") + games("BBBBBB")
    state = play(one_all + points, config)
    assert state.sets[-1] == final_set and state.match_winner is winner


def test_advantage_final_set_has_no_tiebreak() -> None:
    config = ScoreConfig(best_of_sets=3, ad_scoring=True, final_set=FinalSetRule.ADVANTAGE)
    state = play(games("AAAAAA") + games("BBBBBB") + SIX_ALL, config)
    assert state.games == (6, 6) and not state.in_tiebreak


def test_match_tiebreak_is_to_ten_by_two() -> None:
    config = ScoreConfig(best_of_sets=3, ad_scoring=True, final_set=FinalSetRule.MATCH_TIEBREAK_10)
    state = play(games("AAAAAA") + games("BBBBBB"), config)
    assert state.in_tiebreak and points_display(state, config) == "0-0"
    assert play(games("AAAAAA") + games("BBBBBB") + "A" * 9 + "B" * 9 + "A", config).match_winner is None


# ------------------------------------------------------- unresolved, void


def test_unresolved_and_void_do_not_advance() -> None:
    timeline = replay_score(BEST_OF_3, events_for("AUAVU"))
    assert [t.state.points for t in timeline] == [(1, 0), (1, 0), (2, 0), (2, 0), (2, 0)]
    assert [t.state.unresolved_before for t in timeline] == [0, 1, 1, 1, 2]


def test_points_after_the_match_change_nothing() -> None:
    config = ScoreConfig(best_of_sets=1, ad_scoring=True)
    won = play(games("AAAAAA"), config)
    assert won.match_winner is A and won.sets == ((6, 0),)
    assert play(games("AAAAAA") + "BBBB", config) == won


# --------------------------------------------------------------- replay


def correction(event_id: str, sequence: int, target: ScoreEvent, winner: PlayerId | None,
               kind: ScoreEventKind = ScoreEventKind.POINT_WON) -> ScoreEvent:
    return ScoreEvent(event_id, sequence, kind, winner, ScoreSource.USER, target.rally_id, target.event_id)


def test_a_correction_anywhere_equals_a_clean_replay() -> None:
    rng = np.random.default_rng(14)
    config = ScoreConfig(best_of_sets=5, ad_scoring=True)
    for _ in range(40):
        truth = "".join(rng.choice(["A", "B"], size=int(rng.integers(20, 160))))
        wrong = list(truth)
        fixes: list[int] = sorted(set(rng.integers(0, len(truth), size=int(rng.integers(1, 6))).tolist()))
        for index in fixes:
            wrong[index] = "B" if truth[index] == "A" else "A"
        log = events_for("".join(wrong))
        for n, index in enumerate(rng.permutation(fixes)):
            log.append(correction(f"user_{n}", len(truth) + n, log[int(index)], A if truth[int(index)] == "A" else B))
        clean = replay_score(config, events_for(truth))
        corrected = replay_score(config, log)
        assert [t.state for t in corrected] == [t.state for t in clean]
        assert [t.points_display for t in corrected] == [t.points_display for t in clean]


def test_correction_chains_and_conflicts() -> None:
    log = events_for("AAA")
    first = correction("u1", 3, log[1], B)
    second = ScoreEvent("u2", 4, ScoreEventKind.POINT_VOID, None, ScoreSource.USER, "r_0001", "u1")
    effective = effective_events([*log, first, second])
    assert [e.event_id for e in effective] == ["se_0000", "u2", "se_0002"]
    # Two corrections of the same event: the later one wins.
    rival = correction("u3", 5, log[1], A)
    assert [e.event_id for e in effective_events([*log, first, rival])] == ["se_0000", "u3", "se_0002"]
    # An inferred event never supersedes a user one.
    user = events_for("A", source=ScoreSource.USER)
    sneaky = ScoreEvent("se_0009", 9, ScoreEventKind.POINT_WON, B, ScoreSource.INFERRED, None, user[0].event_id)
    assert [e.event_id for e in effective_events([*user, sneaky])] == [user[0].event_id]


def test_replay_is_append_only_and_pure() -> None:
    log = events_for("AABBA")
    snapshot = copy.deepcopy(log)
    first = replay_score(BEST_OF_3, log)
    second = replay_score(BEST_OF_3, log)
    assert log == snapshot and first == second
    extended = replay_score(BEST_OF_3, [*log, correction("u1", 5, log[0], B)])
    assert len(extended) == len(first)  # a correction replaces, it does not add a point
    assert extended[0].state.points == (0, 1)


def test_apply_point_direct() -> None:
    state = apply_point(initial_state(BEST_OF_3), A, BEST_OF_3)
    assert state.points == (1, 0) and SCORE_RULES_VERSION == "score_rules_v1"


# ------------------------------------------------------- full-match table


# Server after N games: the first server serves the odd games (a tiebreak or
# match tiebreak counts as one game), so game N + 1's server. Ends: one change
# per odd game number within a set, one per 6 tiebreak points while it goes
# on, and one at a set's end when its game total is odd.
FULL_MATCHES = [
    # (id, config, points, sets, winner, server after, A's end after)
    # 6 games: game 7 is A's; 3 changes.
    ("bo1 6-0", ScoreConfig(best_of_sets=1, ad_scoring=True), games("AAAAAA"), ((6, 0),), A, A, FAR),
    ("bo3 straight", BEST_OF_3, games("AAAAAA") * 2, ((6, 0), (6, 0)), A, A, NEAR),
    # 33 games: game 34 is B's; 5 + 5 + (6 + 1 + 1) changes = 18.
    ("bo3 6-4 4-6 7-6", BEST_OF_3, games("ABABABABAA") + games("BABABABABB") + SIX_ALL + "A" * 7,
     ((6, 4), (4, 6), (7, 6)), A, B, NEAR),
    # 26 games: game 27 is A's; 3 + 3 + 7 changes = 13.
    ("bo3 final advantage 8-6", ScoreConfig(best_of_sets=3, ad_scoring=True, final_set=FinalSetRule.ADVANTAGE),
     games("AAAAAA") + games("BBBBBB") + SIX_ALL + games("AA"), ((6, 0), (0, 6), (8, 6)), A, A, FAR),
    # 12 games, then the match tiebreak as game 13 (A first): B next; 3 + 3
    # changes, one at 6 points, one at its end (a 1-0 "set" is odd) = 8.
    ("bo3 match tiebreak", ScoreConfig(best_of_sets=3, ad_scoring=False, final_set=FinalSetRule.MATCH_TIEBREAK_10),
     games("BBBBBB") + games("AAAAAA") + "B" * 10, ((0, 6), (6, 0), (0, 1)), B, B, NEAR),
    # 24 games, B first: game 25 is B's; 12 changes from FAR.
    ("bo5 3-1", ScoreConfig(best_of_sets=5, ad_scoring=True, first_server=B, player_a_end_at_start=FAR),
     games("AAAAAA") + games("BBBBBB") + games("AAAAAA") + games("AAAAAA"),
     ((6, 0), (0, 6), (6, 0), (6, 0)), A, B, FAR),
    # As bo1 6-0, each game through a deciding point.
    ("bo1 no-ad deuces", ScoreConfig(best_of_sets=1, ad_scoring=False), "AAABBBA" * 6, ((6, 0),), A, A, FAR),
    # 4-4 then a tiebreak as game 9 (A first): B next; 4 + 1 + 1 changes = 6.
    ("bo1 short sets", ScoreConfig(best_of_sets=1, ad_scoring=True, games_per_set=4, tiebreak_at_games=4),
     games("ABABABAB") + "B" * 7, ((4, 5),), B, B, NEAR),
]


@pytest.mark.parametrize(("name", "config", "points", "sets", "winner", "server", "a_end"), FULL_MATCHES,
                         ids=[row[0] for row in FULL_MATCHES])
def test_full_match_table(name: str, config: ScoreConfig, points: str, sets: tuple[tuple[int, int], ...],
                          winner: PlayerId, server: PlayerId, a_end: CourtEnd) -> None:
    state = play(points, config)
    assert state.sets == sets
    assert state.match_winner is winner
    assert state.server is server
    assert state.player_a_end is a_end
    assert state.games == (0, 0) and state.points == (0, 0)
