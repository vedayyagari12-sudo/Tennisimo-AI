"""Rally segmentation and length (design 6.6, brief G10 and G15).

PURE: stdlib only (plus the event and landing types).

A rally is a maximal time-ordered run of ball activity with no quiet gap over
``max_quiet_s`` (2.5 s). Quiet means no event AND no flight: a long rally
shot is a flight without events and must not split a rally.

Counts (G15, sample kept verbatim):

* ``hits_observed`` = HIT events.
* ``hits_inferred`` = direction reversals with no HIT event.
* ``length_min = hits_observed + hits_inferred``.
* ``length_max = length_min + lost-track gaps``; a lost-track gap is two
  consecutive flights in the run more than 0.2 s apart with no event at the
  join.
* ``length_min <= length_hits <= length_max``; confidence comes from the
  range width only (0 high, 1 medium, 2+ low).

Decisions taken here:

* A direction reversal is read from bounce halves, which are valid ground
  positions (6.6): two consecutive bounces with no HIT between them on
  DIFFERENT halves mean an unseen hit after the first. The serve or feed is a
  reversal too (the ball starts moving): when the run's first ball contact
  is not a HIT, one hit is inferred for it. Row trends of bounce-less
  segments are not used.
* ``length_hits`` is ``length_min``: a lost-track gap MAY hide a hit, but
  nothing observed says it did.
* End reason, judged on the last shot (contacts after the last HIT): a net
  contact, or a first bounce on the hitter's OWN half (the ball never
  crossed: T0's net balls touch low and bounce 20 ms later, one break) ->
  ``net``; no bounce at all (its bounce never seen) -> ``timeout``; the
  first bounce confidently OUT -> ``ball_out`` (an out ball bouncing on
  stays out); the first two bounces on one half -> ``double_bounce``;
  anything else (in, too close or uncalled, then nothing) -> ``lost_track``.
* ``segment_rallies`` and ``rally_length`` take ``flights`` (for quiet and
  lost-track gaps) and read bounce halves from the landings; the design 6.5
  signatures had neither.
* Two flights overlapping in time in one run flag ``two_balls``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.analysis.ball_events import BallEvent
from app.analysis.court_model import CourtEnd
from app.analysis.flight_track import BallFlight
from app.analysis.placement import ShotLanding
from app.models.court import BallEventKind, LengthConfidence, LineVerdict, RallyEndReason

__all__ = [
    "RALLY_VERSION",
    "LengthConfidence",
    "Rally",
    "RallyEndReason",
    "group_event_runs",
    "preceding_hits",
    "rally_length",
    "segment_rallies",
]

RALLY_VERSION: Final[str] = "rally_v1"
MAX_QUIET_S: Final[float] = 2.5
#: G15: flights further apart than this, with no event at the join, are a gap.
LOST_TRACK_GAP_S: Final[float] = 0.2
#: An event this close to a join counts as "at the join".
JOIN_EVENT_TOLERANCE_S: Final[float] = 0.05
TWO_BALLS_FLAG: Final[str] = "two_balls"
LOST_TRACK_GAP_FLAG: Final[str] = "lost_track_gap"


@dataclass(frozen=True, eq=False)
class Rally:
    rally_id: str
    start_s: float
    end_s: float
    length_hits: int
    length_min: int
    length_max: int
    confidence: LengthConfidence
    hits_observed: int
    hits_inferred: int
    end_reason: RallyEndReason
    event_ids: tuple[str, ...]
    landings: tuple[ShotLanding, ...]
    flags: tuple[str, ...]


def _flight_span(flight: BallFlight) -> tuple[float, float]:
    found = np.flatnonzero(flight.detected)
    return float(flight.times_s[found[0]]), float(flight.times_s[found[-1]])


def _activity_runs(
    events: Sequence[BallEvent], flights: Sequence[BallFlight], max_quiet_s: float
) -> list[tuple[float, float, list[BallEvent], list[BallFlight]]]:
    """(start, end, events, flights) per run of activity, in time order."""
    items: list[tuple[float, float, int, BallEvent | None, BallFlight | None]] = []
    for event in events:
        items.append((event.time_s, event.time_s, 0, event, None))
    for flight in flights:
        if np.any(flight.detected):
            start, end = _flight_span(flight)
            items.append((start, end, 1, None, flight))
    items.sort(key=lambda item: (item[0], item[1], item[2]))
    runs: list[tuple[float, float, list[BallEvent], list[BallFlight]]] = []
    for start, end, _, event, flight in items:
        if not runs or start - runs[-1][1] > max_quiet_s:
            runs.append((start, end, [], []))
        run = runs[-1]
        runs[-1] = (run[0], max(run[1], end), run[2], run[3])
        if event is not None:
            run[2].append(event)
        if flight is not None:
            run[3].append(flight)
    return runs


def group_event_runs(
    events: Sequence[BallEvent],
    *,
    max_quiet_s: float = MAX_QUIET_S,
    flights: Sequence[BallFlight] = (),
) -> tuple[tuple[BallEvent, ...], ...]:
    """Brief G10: events split where nothing (no event, no flight) happens
    for more than ``max_quiet_s``. Runs with no event are dropped."""
    ordered = sorted(events, key=lambda e: (e.time_s, e.event_id))
    return tuple(tuple(run[2]) for run in _activity_runs(ordered, flights, max_quiet_s) if run[2])


def preceding_hits(run: Sequence[BallEvent]) -> Mapping[str, BallEvent | None]:
    """Brief G10: for each BOUNCE in a run, the last HIT before it (None if
    there is none, which makes its target ``half_only``)."""
    last: BallEvent | None = None
    out: dict[str, BallEvent | None] = {}
    for event in sorted(run, key=lambda e: (e.time_s, e.event_id)):
        if event.kind is BallEventKind.HIT:
            last = event
        elif event.kind is BallEventKind.BOUNCE:
            out[event.event_id] = last
    return out


def _lost_track_gaps(run_events: Sequence[BallEvent], flights: Sequence[BallFlight]) -> int:
    spans = sorted(_flight_span(f) for f in flights if np.any(f.detected))
    gaps = 0
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        if start - end <= LOST_TRACK_GAP_S:
            continue
        at_join = any(
            end - JOIN_EVENT_TOLERANCE_S <= e.time_s <= start + JOIN_EVENT_TOLERANCE_S for e in run_events
        )
        if not at_join:
            gaps += 1
    return gaps


def _two_balls(flights: Sequence[BallFlight]) -> bool:
    spans = sorted(_flight_span(f) for f in flights if np.any(f.detected))
    return any(next_start < end for (_, end), (next_start, _) in zip(spans, spans[1:], strict=False))


def _confidence(width: int) -> LengthConfidence:
    if width <= 0:
        return LengthConfidence.HIGH
    if width == 1:
        return LengthConfidence.MEDIUM
    return LengthConfidence.LOW


def rally_length(
    events: Sequence[BallEvent],
    landings: Sequence[ShotLanding],
    flights: Sequence[BallFlight] = (),
) -> tuple[int, int, int, LengthConfidence]:
    """(length_hits, length_min, length_max, confidence) for one run."""
    observed, inferred, gaps = _counts(events, _halves(landings), flights)
    length_min = observed + inferred
    return length_min, length_min, length_min + gaps, _confidence(gaps)


def _halves(landings: Sequence[ShotLanding]) -> dict[str, CourtEnd]:
    return {landing.bounce.event_id: landing.bounce.half for landing in landings}


def _counts(
    events: Sequence[BallEvent], halves: Mapping[str, CourtEnd], flights: Sequence[BallFlight]
) -> tuple[int, int, int]:
    ordered = sorted(events, key=lambda e: (e.time_s, e.event_id))
    contacts = [e for e in ordered if e.kind in (BallEventKind.HIT, BallEventKind.BOUNCE)]
    observed = sum(1 for e in contacts if e.kind is BallEventKind.HIT)
    inferred = 0
    if contacts and contacts[0].kind is not BallEventKind.HIT:
        inferred += 1  # the serve or feed that launched the ball
    for first, second in zip(contacts, contacts[1:], strict=False):
        if first.kind is BallEventKind.BOUNCE and second.kind is BallEventKind.BOUNCE:
            a, b = halves.get(first.event_id), halves.get(second.event_id)
            if a is not None and b is not None and a is not b:
                inferred += 1
    return observed, inferred, _lost_track_gaps(ordered, flights)


def _end_reason(
    events: Sequence[BallEvent], landings: Mapping[str, ShotLanding], halves: Mapping[str, CourtEnd]
) -> RallyEndReason:
    """Judged at the last shot: the contacts after the run's last HIT (or
    after the launch, when there is none). The point is decided at that
    shot's FIRST bounce, so an out ball that bounces on is still out."""
    ordered = sorted(events, key=lambda e: (e.time_s, e.event_id))
    contacts = [e for e in ordered if e.kind is not BallEventKind.UNKNOWN_KINK]
    if not contacts:
        return RallyEndReason.LOST_TRACK
    last_hit = max((i for i, e in enumerate(contacts) if e.kind is BallEventKind.HIT), default=-1)
    after = contacts[last_hit + 1 :]
    if any(e.kind is BallEventKind.NET_CONTACT for e in after):
        return RallyEndReason.NET
    bounces = [e for e in after if e.kind is BallEventKind.BOUNCE]
    if not bounces:
        return RallyEndReason.TIMEOUT
    first = landings.get(bounces[0].event_id)
    if first is not None and first.hitter_end is not None and first.bounce.half is first.hitter_end:
        return RallyEndReason.NET
    if first is not None and first.call is not None and first.call.verdict is LineVerdict.OUT:
        return RallyEndReason.BALL_OUT
    if len(bounces) >= 2:
        a, b = halves.get(bounces[0].event_id), halves.get(bounces[1].event_id)
        if a is not None and a is b:
            return RallyEndReason.DOUBLE_BOUNCE
    return RallyEndReason.LOST_TRACK


def segment_rallies(
    events: Sequence[BallEvent],
    landings: Sequence[ShotLanding],
    *,
    flights: Sequence[BallFlight] = (),
    max_quiet_s: float = MAX_QUIET_S,
) -> tuple[Rally, ...]:
    """Rallies in time order, ids ``r_{index:04d}`` (brief G9). A run of
    activity with no ball contact event is not a rally."""
    ordered = sorted(events, key=lambda e: (e.time_s, e.event_id))
    by_bounce = {landing.bounce.event_id: landing for landing in landings}
    halves = _halves(landings)
    rallies: list[Rally] = []
    for start, end, run_events, run_flights in _activity_runs(ordered, flights, max_quiet_s):
        if not any(e.kind in (BallEventKind.HIT, BallEventKind.BOUNCE) for e in run_events):
            continue
        observed, inferred, gaps = _counts(run_events, halves, run_flights)
        length_min = observed + inferred
        flags: list[str] = []
        if gaps:
            flags.append(LOST_TRACK_GAP_FLAG)
        if _two_balls(run_flights):
            flags.append(TWO_BALLS_FLAG)
        rallies.append(
            Rally(
                rally_id=f"r_{len(rallies):04d}",
                start_s=start,
                end_s=end,
                length_hits=length_min,
                length_min=length_min,
                length_max=length_min + gaps,
                confidence=_confidence(gaps),
                hits_observed=observed,
                hits_inferred=inferred,
                end_reason=_end_reason(run_events, by_bounce, halves),
                event_ids=tuple(e.event_id for e in run_events),
                landings=tuple(by_bounce[e.event_id] for e in run_events if e.event_id in by_bounce),
                flags=tuple(flags),
            )
        )
    return tuple(rallies)
