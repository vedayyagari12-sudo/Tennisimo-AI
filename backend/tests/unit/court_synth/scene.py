"""Seeded rally and match scenes with exact truth (design 11.1). Test-only.

Every shot is solved so the physics lands it on a chosen target, but the TRUTH
is whatever the physics then produced: bounce times and points come from the
integrator's contact events, never from the aim.

Scenarios: clean rally, serve fault, double fault, net ball, double bounce,
far-half occlusion, second ball, adjacent-court decoy, bird-like arc, unseen
far bounce. ``generate_match`` strings rallies together; it carries per-point
truth (winner END, server end, serve side) but NO tennis score, because the
score rules are build step 14's to implement and must not be borrowed here.
Server end and serve side simply alternate per rally.

All randomness is ``np.random.default_rng(seed)``, consumed in a fixed order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

from app.analysis.court_model import (
    HALF_LENGTH_M,
    SERVICE_LINE_FROM_NET_M,
    CourtEnd,
    CourtFormat,
    CourtRegion,
    half_region,
    service_box_region,
)
from app.models.court import LineCallTarget, RallyEndReason
from tests.unit.court_synth.physics import (
    BALL_RADIUS_M,
    GRAVITY_M_S2,
    STEP_S,
    FlightEventKind,
    Trajectory,
    simulate,
)
from tests.unit.court_synth.truth import is_in, signed_margin_m

Float64Array = npt.NDArray[np.float64]
ServeSide = Literal["deuce", "ad"]

#: Adjacent court centre: a doubles width plus a 3.66 m gap [assumption].
ADJACENT_COURT_X_M: Final[float] = 10.97 + 3.66
#: Tail of ball flight simulated after a rally's terminal event.
RALLY_TAIL_S: Final[float] = 1.2
#: Reaction from a bounce to the opponent's hit, seconds.
HIT_AFTER_BOUNCE_S: Final[tuple[float, float]] = (0.30, 0.55)
PLAYER_BASELINE_OFFSET_M: Final[float] = 0.5
BIRD_RADIUS_M: Final[float] = 0.12
BALL_COLOUR_BGR: Final[tuple[int, int, int]] = (60, 225, 215)
BIRD_COLOUR_BGR: Final[tuple[int, int, int]] = (45, 45, 50)
_AIM_ITERATIONS: Final[int] = 14
_AIM_TOLERANCE_M: Final[float] = 1e-3
_AIM_TOLERANCE_S: Final[float] = 1e-3
#: Attempts at a higher, slower arc when an aimed shot would hit the net.
_NET_CLEARANCE_TRIES: Final[int] = 8


class Scenario(StrEnum):
    CLEAN_RALLY = "clean_rally"
    SERVE_FAULT = "serve_fault"
    DOUBLE_FAULT = "double_fault"
    NET_BALL = "net_ball"
    DOUBLE_BOUNCE = "double_bounce"
    FAR_HALF_OCCLUSION = "far_half_occlusion"
    SECOND_BALL = "second_ball"
    ADJACENT_COURT_DECOY = "adjacent_court_decoy"
    BIRD_LIKE_ARC = "bird_like_arc"
    UNSEEN_FAR_BOUNCE = "unseen_far_bounce"


class BallRole(StrEnum):
    MAIN = "main"
    SECOND_BALL = "second_ball"
    ADJACENT_DECOY = "adjacent_decoy"
    BIRD = "bird"


@dataclass(frozen=True)
class TruthHit:
    time_s: float
    end: CourtEnd
    position_m: tuple[float, float, float]
    is_serve: bool
    shot_index: int  # 1-based within the rally


@dataclass(frozen=True)
class TruthBounce:
    time_s: float
    position_m: tuple[float, float, float]  # ball centre at contact
    half: CourtEnd
    hitter_end: CourtEnd
    shot_index: int
    bounce_index: int  # 1 = first bounce after that shot
    target: LineCallTarget
    target_region: CourtRegion
    signed_margin_m: float
    is_in: bool
    visible: bool

    @property
    def court_xy_m(self) -> tuple[float, float]:
        return (self.position_m[0], self.position_m[1])


@dataclass(frozen=True)
class TruthNetContact:
    time_s: float
    position_m: tuple[float, float, float]
    hitter_end: CourtEnd


@dataclass(frozen=True)
class RallyTruth:
    rally_index: int
    scenario: Scenario
    server_end: CourtEnd
    serve_side: ServeSide
    start_s: float
    end_s: float
    hits: tuple[TruthHit, ...]
    bounces: tuple[TruthBounce, ...]
    net_contacts: tuple[TruthNetContact, ...]
    length_hits: int
    end_reason: RallyEndReason
    #: End of the player who won the point; None when no point is decided
    #: (a first-serve fault).
    point_winner_end: CourtEnd | None
    is_serve_fault: bool


@dataclass(frozen=True, eq=False)
class BallTruth:
    ball_id: int
    role: BallRole
    trajectory: Trajectory
    radius_m: float
    colour_bgr: tuple[int, int, int]


@dataclass(frozen=True, eq=False)
class PlayerTrack:
    end: CourtEnd
    times_s: Float64Array
    xy_m: Float64Array

    def position_at(self, time_s: float) -> tuple[float, float]:
        return (
            float(np.interp(time_s, self.times_s, self.xy_m[:, 0])),
            float(np.interp(time_s, self.times_s, self.xy_m[:, 1])),
        )


@dataclass(frozen=True, eq=False)
class SceneTruth:
    seed: int
    scenarios: tuple[Scenario, ...]
    court_format: CourtFormat
    duration_s: float
    rallies: tuple[RallyTruth, ...]
    balls: tuple[BallTruth, ...]
    players: tuple[PlayerTrack, ...]
    #: Intervals in which MAIN balls are hidden from the camera.
    occlusions: tuple[tuple[float, float], ...]

    def ball_visible(self, ball: BallTruth, time_s: float) -> bool:
        if ball.role is not BallRole.MAIN:
            return True
        return not any(start <= time_s <= end for start, end in self.occlusions)

    @property
    def bounces(self) -> tuple[TruthBounce, ...]:
        return tuple(b for rally in self.rallies for b in rally.bounces)


def _other(end: CourtEnd) -> CourtEnd:
    return CourtEnd.FAR if end is CourtEnd.NEAR else CourtEnd.NEAR


def _end_sign(end: CourtEnd) -> float:
    return -1.0 if end is CourtEnd.NEAR else 1.0


def _on_grid(time_s: float) -> float:
    return round(time_s / STEP_S) * STEP_S


def aim_shot(
    launch_m: Float64Array, target_xy_m: tuple[float, float], flight_s: float
) -> Float64Array:
    """Launch velocity landing (first bounce) on ``target_xy_m`` after about
    ``flight_s``, under drag, ignoring the net. Fixed-point correction from the
    drag-free solution."""
    launch = np.asarray(launch_m, dtype=np.float64)
    target = np.asarray(target_xy_m, dtype=np.float64)
    velocity = np.empty(3)
    velocity[:2] = (target - launch[:2]) / flight_s
    velocity[2] = (BALL_RADIUS_M - launch[2] + 0.5 * GRAVITY_M_S2 * flight_s**2) / flight_s
    for _ in range(_AIM_ITERATIONS):
        trajectory = simulate(
            launch, velocity, 0.0, 4.0 * flight_s, stop_after_bounces=1, collide_net=False
        )
        bounces = [e for e in trajectory.events if e.kind is FlightEventKind.BOUNCE]
        if not bounces:
            velocity[2] -= 1.0
            continue
        landed = np.array(bounces[0].position_m[:2])
        miss = target - landed
        late = flight_s - bounces[0].time_s
        if float(np.hypot(*miss)) < _AIM_TOLERANCE_M and abs(late) < _AIM_TOLERANCE_S:
            break
        velocity[:2] += miss / flight_s
        velocity[2] += 0.5 * GRAVITY_M_S2 * late
    return velocity


def clears_net(launch_m: Float64Array, velocity: Float64Array, flight_s: float) -> bool:
    check = simulate(launch_m, velocity, 0.0, 2.0 * flight_s, stop_after_bounces=1)
    return not any(e.kind is FlightEventKind.NET for e in check.events)


class _RallyBuilder:
    """Simulates one rally shot by shot and records its truth."""

    def __init__(
        self,
        rng: np.random.Generator,
        *,
        rally_index: int,
        scenario: Scenario,
        start_s: float,
        server_end: CourtEnd,
        serve_side: ServeSide,
        court_format: CourtFormat,
    ) -> None:
        self.rng = rng
        self.rally_index = rally_index
        self.scenario = scenario
        self.start_s = _on_grid(start_s)
        self.server_end = server_end
        self.serve_side = serve_side
        self.court_format = court_format

    # ------------------------------------------------------------------ targets
    def _serve_target(self, fault: bool) -> tuple[tuple[float, float], CourtRegion]:
        receiver = _other(self.server_end)
        box = service_box_region(receiver, self.serve_side)
        x = float(self.rng.uniform(box.x_min + 0.4, box.x_max - 0.4))
        if fault:
            depth = SERVICE_LINE_FROM_NET_M + float(self.rng.uniform(0.4, 1.5))
        else:
            depth = float(self.rng.uniform(3.0, SERVICE_LINE_FROM_NET_M - 0.4))
        return (x, _end_sign(receiver) * depth), box

    def _rally_target(self, hitter: CourtEnd, kind: str) -> tuple[tuple[float, float], CourtRegion]:
        receiver = _other(hitter)
        region = half_region(receiver, self.court_format)
        sign = _end_sign(receiver)
        if kind == "in":
            x = float(self.rng.uniform(region.x_min + 0.7, region.x_max - 0.7))
            depth = float(self.rng.uniform(4.0, HALF_LENGTH_M - 0.8))
        elif kind == "drop":
            x = float(self.rng.uniform(-2.5, 2.5))
            depth = float(self.rng.uniform(1.5, 3.0))
        else:  # out: long or wide
            if self.rng.uniform() < 0.5:
                x = float(self.rng.uniform(-3.0, 3.0))
                depth = HALF_LENGTH_M + float(self.rng.uniform(0.4, 1.5))
            else:
                side = 1.0 if self.rng.uniform() < 0.5 else -1.0
                x = side * (region.x_max + float(self.rng.uniform(0.3, 1.0)))
                depth = float(self.rng.uniform(5.0, 10.0))
        return (x, sign * depth), region

    # -------------------------------------------------------------------- build
    def build(self) -> tuple[RallyTruth, BallTruth, list[TruthHit]]:
        plan = self._plan()
        hitter = self.server_end
        side_sign = 1.0 if (self.server_end is CourtEnd.NEAR) == (self.serve_side == "deuce") else -1.0
        launch = np.array(
            [
                side_sign * float(self.rng.uniform(0.4, 1.2)),
                _end_sign(hitter) * (HALF_LENGTH_M + 0.3),
                2.7,
            ]
        )
        time_s = self.start_s
        segments: list[Trajectory] = []
        hits: list[TruthHit] = []
        bounces: list[TruthBounce] = []
        nets: list[TruthNetContact] = []
        for shot_index, kind in enumerate(plan, start=1):
            is_serve = shot_index == 1
            hit = TruthHit(
                time_s=time_s,
                end=hitter,
                position_m=(float(launch[0]), float(launch[1]), float(launch[2])),
                is_serve=is_serve,
                shot_index=shot_index,
            )
            hits.append(hit)
            last = shot_index == len(plan)
            velocity, target_kind, region = self._solve(hitter, launch, kind, is_serve)
            duration = 6.0 if last else 4.0
            segment = simulate(launch, velocity, time_s, duration)
            first_bounce = next(
                (e for e in segment.events if e.kind is FlightEventKind.BOUNCE), None
            )
            if last:
                segment = self._trim_tail(segment)
            else:
                assert first_bounce is not None
                next_time = _on_grid(
                    first_bounce.time_s + float(self.rng.uniform(*HIT_AFTER_BOUNCE_S))
                )
                keep = segment.times_s <= next_time + 1e-9
                events = tuple(e for e in segment.events if e.time_s <= next_time)
                segment = Trajectory(segment.times_s[keep], segment.positions_m[keep], events)
            segments.append(segment)
            bounce_index = 0
            for event in segment.events:
                if event.kind is FlightEventKind.NET:
                    nets.append(TruthNetContact(event.time_s, event.position_m, hitter))
                    continue
                bounce_index += 1
                xy = (event.position_m[0], event.position_m[1])
                margin = signed_margin_m(xy, region)
                bounces.append(
                    TruthBounce(
                        time_s=event.time_s,
                        position_m=event.position_m,
                        half=CourtEnd.NEAR if xy[1] < 0.0 else CourtEnd.FAR,
                        hitter_end=hitter,
                        shot_index=shot_index,
                        bounce_index=bounce_index,
                        target=target_kind,
                        target_region=region,
                        signed_margin_m=margin,
                        is_in=is_in(margin),
                        visible=True,
                    )
                )
            if not last:
                time_s = float(segment.times_s[-1])
                launch = segment.positions_m[-1].copy()
                hitter = _other(hitter)
        trajectory = self._concatenate(segments)
        truth = self._finish(hits, bounces, nets, trajectory)
        ball = BallTruth(
            ball_id=self.rally_index,
            role=BallRole.MAIN,
            trajectory=trajectory,
            radius_m=BALL_RADIUS_M,
            colour_bgr=BALL_COLOUR_BGR,
        )
        return truth, ball, hits

    def _plan(self) -> list[str]:
        """Shot kinds: serve_in / serve_fault, then in / out / drop / net."""
        if self.scenario in (Scenario.SERVE_FAULT, Scenario.DOUBLE_FAULT):
            return ["serve_fault"]
        count = int(self.rng.integers(3, 7))
        if self.scenario is Scenario.UNSEEN_FAR_BOUNCE:
            # The last hitter must be the near player, so the final bounce is far.
            near_serves = self.server_end is CourtEnd.NEAR
            if (count % 2 == 1) != near_serves:
                count += 1
        finals = {
            Scenario.NET_BALL: "net",
            Scenario.DOUBLE_BOUNCE: "drop",
            Scenario.UNSEEN_FAR_BOUNCE: "drop",
        }
        return ["serve_in"] + ["in"] * (count - 2) + [finals.get(self.scenario, "out")]

    def _solve(
        self, hitter: CourtEnd, launch: Float64Array, kind: str, is_serve: bool
    ) -> tuple[Float64Array, LineCallTarget, CourtRegion]:
        if is_serve:
            target, region = self._serve_target(fault=kind == "serve_fault")
            flight = float(np.hypot(*(np.array(target) - launch[:2]))) / float(
                self.rng.uniform(26.0, 34.0)
            )
            target_kind = (
                LineCallTarget.SERVICE_BOX_DEUCE
                if self.serve_side == "deuce"
                else LineCallTarget.SERVICE_BOX_AD
            )
            return self._aim_clear(launch, target, flight, 0.08), target_kind, region
        target_kind = (
            LineCallTarget.SINGLES_HALF
            if self.court_format is CourtFormat.SINGLES
            else LineCallTarget.DOUBLES_HALF
        )
        if kind == "net":
            # Aimed (net ignored) to touch down just past the net line, so it
            # reaches y = 0 barely above the ground: certain net contact.
            receiver = _other(hitter)
            target = (float(self.rng.uniform(-2.0, 2.0)), _end_sign(receiver) * 0.3)
            region = half_region(receiver, self.court_format)
            return aim_shot(launch, target, float(self.rng.uniform(0.7, 0.9))), target_kind, region
        target, region = self._rally_target(hitter, kind)
        flight = float(self.rng.uniform(1.0, 1.3)) if kind == "drop" else float(
            self.rng.uniform(0.9, 1.4)
        )
        return self._aim_clear(launch, target, flight, 0.15), target_kind, region

    @staticmethod
    def _aim_clear(
        launch: Float64Array, target: tuple[float, float], flight_s: float, step_s: float
    ) -> Float64Array:
        """Aim, lengthening the flight (a higher arc) until the net is cleared.

        Raises:
            RuntimeError: if no tried arc clears the net; a scene bug, not data.
        """
        for _ in range(_NET_CLEARANCE_TRIES):
            velocity = aim_shot(launch, target, flight_s)
            if clears_net(launch, velocity, flight_s):
                return velocity
            flight_s += step_s
        raise RuntimeError(f"no arc from {launch} to {target} clears the net")

    def _trim_tail(self, segment: Trajectory) -> Trajectory:
        """End the last segment RALLY_TAIL_S after its terminal event (the
        second bounce for a drop shot, the first otherwise)."""
        terminal_events = [e for e in segment.events if e.kind is FlightEventKind.BOUNCE]
        wanted = 2 if self.scenario in (Scenario.DOUBLE_BOUNCE, Scenario.UNSEEN_FAR_BOUNCE) else 1
        if self.scenario is Scenario.NET_BALL:
            wanted = 1
        anchor = (
            terminal_events[wanted - 1].time_s
            if len(terminal_events) >= wanted
            else float(segment.times_s[-1]) - RALLY_TAIL_S
        )
        end = anchor + RALLY_TAIL_S
        keep = segment.times_s <= end + 1e-9
        return Trajectory(
            segment.times_s[keep],
            segment.positions_m[keep],
            tuple(e for e in segment.events if e.time_s <= end),
        )

    @staticmethod
    def _concatenate(segments: Sequence[Trajectory]) -> Trajectory:
        times = [s.times_s[:-1] for s in segments[:-1]] + [segments[-1].times_s]
        positions = [s.positions_m[:-1] for s in segments[:-1]] + [segments[-1].positions_m]
        events = tuple(e for s in segments for e in s.events)
        return Trajectory(np.concatenate(times), np.concatenate(positions), events)

    def _finish(
        self,
        hits: list[TruthHit],
        bounces: list[TruthBounce],
        nets: list[TruthNetContact],
        trajectory: Trajectory,
    ) -> RallyTruth:
        last_hitter = hits[-1].end
        fault = self.scenario in (Scenario.SERVE_FAULT, Scenario.DOUBLE_FAULT)
        if fault:
            reason, winner = RallyEndReason.BALL_OUT, None
        elif self.scenario is Scenario.NET_BALL:
            reason, winner = RallyEndReason.NET, _other(last_hitter)
        elif self.scenario in (Scenario.DOUBLE_BOUNCE, Scenario.UNSEEN_FAR_BOUNCE):
            reason, winner = RallyEndReason.DOUBLE_BOUNCE, last_hitter
        else:
            reason, winner = RallyEndReason.BALL_OUT, _other(last_hitter)
        return RallyTruth(
            rally_index=self.rally_index,
            scenario=self.scenario,
            server_end=self.server_end,
            serve_side=self.serve_side,
            start_s=self.start_s,
            end_s=float(trajectory.times_s[-1]),
            hits=tuple(hits),
            bounces=tuple(bounces),
            net_contacts=tuple(nets),
            length_hits=len(hits),
            end_reason=reason,
            point_winner_end=winner,
            is_serve_fault=fault,
        )


def _second_ball(rng: np.random.Generator, start_s: float, ball_id: int) -> BallTruth:
    """A second real ball crossing far -> near while the rally is live."""
    launch = np.array([float(rng.uniform(-3.0, 3.0)), HALF_LENGTH_M + 0.5, 1.0])
    target = (float(rng.uniform(-3.0, 3.0)), -float(rng.uniform(4.0, 9.0)))
    velocity = aim_shot(launch, target, 1.2)
    trajectory = simulate(launch, velocity, _on_grid(start_s), 2.5, collide_net=False)
    return BallTruth(ball_id, BallRole.SECOND_BALL, trajectory, BALL_RADIUS_M, BALL_COLOUR_BGR)


def _adjacent_decoy(rng: np.random.Generator, start_s: float, ball_id: int) -> BallTruth:
    """A ball in play on the next court, crossing its own net."""
    side = 1.0 if rng.uniform() < 0.5 else -1.0
    centre = side * ADJACENT_COURT_X_M
    launch = np.array([centre + float(rng.uniform(-2.0, 2.0)), -12.0, 1.0])
    target = (centre + float(rng.uniform(-3.0, 3.0)), float(rng.uniform(5.0, 10.0)))
    velocity = aim_shot(launch, target, 1.2)
    trajectory = simulate(launch, velocity, _on_grid(start_s), 3.0, collide_net=False)
    return BallTruth(ball_id, BallRole.ADJACENT_DECOY, trajectory, BALL_RADIUS_M, BALL_COLOUR_BGR)


def _bird(rng: np.random.Generator, start_s: float, ball_id: int) -> BallTruth:
    """A smooth, never-bouncing arc well above the court."""
    t0 = _on_grid(start_s)
    times = t0 + np.arange(int(round(4.0 / STEP_S)) + 1) * STEP_S
    elapsed = times - t0
    y = float(rng.uniform(5.0, 20.0))
    z0 = float(rng.uniform(5.0, 9.0))
    positions = np.column_stack(
        [
            -25.0 + 12.0 * elapsed,
            np.full_like(elapsed, y) + 1.5 * np.sin(0.8 * elapsed),
            z0 + 1.2 * np.sin(1.1 * elapsed),
        ]
    )
    return BallTruth(
        ball_id, BallRole.BIRD, Trajectory(times, positions, ()), BIRD_RADIUS_M, BIRD_COLOUR_BGR
    )


def _player_tracks(hits: Sequence[TruthHit], duration_s: float) -> tuple[PlayerTrack, ...]:
    tracks = []
    for end in (CourtEnd.NEAR, CourtEnd.FAR):
        baseline_y = _end_sign(end) * (HALF_LENGTH_M + PLAYER_BASELINE_OFFSET_M)
        own = [(h.time_s, h.position_m[0], h.position_m[1]) for h in hits if h.end is end]
        times = [0.0] + [t for t, _, _ in own] + [duration_s]
        xs = [0.0] + [x for _, x, _ in own] + [own[-1][1] if own else 0.0]
        ys = [baseline_y] + [y for _, _, y in own] + [own[-1][2] if own else baseline_y]
        order = np.argsort(times, kind="stable")
        tracks.append(
            PlayerTrack(
                end=end,
                times_s=np.asarray(times, dtype=np.float64)[order],
                xy_m=np.column_stack([np.asarray(xs)[order], np.asarray(ys)[order]]),
            )
        )
    return tuple(tracks)


def _occlusion_for(scenario: Scenario, rally: RallyTruth) -> tuple[float, float] | None:
    if scenario is Scenario.FAR_HALF_OCCLUSION:
        far = [b for b in rally.bounces if b.half is CourtEnd.FAR and b.bounce_index == 1]
        if far:
            return (far[0].time_s - 0.30, far[0].time_s - 0.08)
    if scenario is Scenario.UNSEEN_FAR_BOUNCE:
        final = [b for b in rally.bounces if b.shot_index == rally.length_hits]
        if final:
            return (final[0].time_s - 0.15, rally.end_s + 1.0)
    return None


def generate_match(
    seed: int,
    scenarios: Sequence[Scenario],
    *,
    court_format: CourtFormat = CourtFormat.SINGLES,
    first_server_end: CourtEnd = CourtEnd.NEAR,
    first_start_s: float = 1.0,
) -> SceneTruth:
    """Rallies in sequence, separated by 4-6 s of quiet (over the 2.5 s rally
    split). A DOUBLE_FAULT scenario yields two rallies: the first fault (no
    point) and the second (point to the receiver)."""
    rng = np.random.default_rng(seed)
    rallies: list[RallyTruth] = []
    balls: list[BallTruth] = []
    all_hits: list[TruthHit] = []
    occlusions: list[tuple[float, float]] = []
    server = first_server_end
    side: ServeSide = "deuce"
    start = first_start_s
    for scenario in scenarios:
        faults = 2 if scenario is Scenario.DOUBLE_FAULT else 1
        for fault_number in range(faults):
            builder = _RallyBuilder(
                rng,
                rally_index=len(rallies),
                scenario=scenario,
                start_s=start,
                server_end=server,
                serve_side=side,
                court_format=court_format,
            )
            rally, ball, hits = builder.build()
            if scenario is Scenario.DOUBLE_FAULT and fault_number == 1:
                rally = _with_winner(rally, _other(server))
            occlusion = _occlusion_for(scenario, rally)
            if occlusion is not None:
                occlusions.append(occlusion)
                rally = _mark_hidden(rally, occlusion)
            rallies.append(rally)
            balls.append(ball)
            all_hits.extend(hits)
            extra_id = 1000 + len(balls)
            if scenario is Scenario.SECOND_BALL:
                balls.append(_second_ball(rng, rally.start_s + float(rng.uniform(0.5, 1.5)), extra_id))
            elif scenario is Scenario.ADJACENT_COURT_DECOY:
                balls.append(_adjacent_decoy(rng, rally.start_s + float(rng.uniform(0.3, 1.0)), extra_id))
            elif scenario is Scenario.BIRD_LIKE_ARC:
                balls.append(_bird(rng, rally.start_s + float(rng.uniform(0.0, 1.0)), extra_id))
            start = rally.end_s + float(rng.uniform(4.0, 6.0))
        server = _other(server)
        side = "ad" if side == "deuce" else "deuce"
    duration = max(float(b.trajectory.end_s) for b in balls) + 0.5
    return SceneTruth(
        seed=seed,
        scenarios=tuple(scenarios),
        court_format=court_format,
        duration_s=duration,
        rallies=tuple(rallies),
        balls=tuple(balls),
        players=_player_tracks(all_hits, duration),
        occlusions=tuple(occlusions),
    )


def generate_rally(
    seed: int,
    scenario: Scenario = Scenario.CLEAN_RALLY,
    *,
    court_format: CourtFormat = CourtFormat.SINGLES,
    server_end: CourtEnd = CourtEnd.NEAR,
    start_s: float = 1.0,
) -> SceneTruth:
    """One scenario (two rallies for DOUBLE_FAULT)."""
    return generate_match(
        seed,
        [scenario],
        court_format=court_format,
        first_server_end=server_end,
        first_start_s=start_s,
    )


def _with_winner(rally: RallyTruth, winner: CourtEnd) -> RallyTruth:
    return replace(rally, point_winner_end=winner)


def _mark_hidden(rally: RallyTruth, occlusion: tuple[float, float]) -> RallyTruth:
    start, end = occlusion
    bounces = tuple(
        replace(b, visible=not (start <= b.time_s <= end))
        for b in rally.bounces
    )
    return replace(rally, bounces=bounces)


__all__ = [
    "BallRole",
    "BallTruth",
    "PlayerTrack",
    "RallyTruth",
    "Scenario",
    "SceneTruth",
    "TruthBounce",
    "TruthHit",
    "TruthNetContact",
    "aim_shot",
    "generate_match",
    "generate_rally",
]
