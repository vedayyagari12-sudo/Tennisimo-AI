"""3D ball physics for the synthetic court (design 11.1). Test-only.

RK4 at 1/960 s, gravity 9.81, quadratic drag a = -k |v| v with k = 0.0204 per
metre (1/2 rho C_d A / m, PIPELINE.md 11.2). Bounce: vertical restitution
0.75, horizontal retention 0.65 [assumptions]. Net collision at y = 0 below
the net. NO Magnus: spin is out of scope, so real topspin lands differently.

Contact instants (bounce, net) are found inside the step by bisection, so the
recorded event time and position are exact to the integrator, not snapped to
the 960 Hz grid. Samples stay on the uniform grid.

The integrator runs on plain float tuples: at 960 Hz, numpy's per-call
overhead on 3-vectors made scene generation ~10x slower for no accuracy gain.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np
import numpy.typing as npt

from app.analysis.court_model import (
    NET_HEIGHT_CENTRE_M,
    NET_HEIGHT_POST_M,
    NET_POST_HALF_SPAN_M,
)

Float64Array = npt.NDArray[np.float64]
Vec3 = tuple[float, float, float]

GRAVITY_M_S2: Final[float] = 9.81
DRAG_K_PER_M: Final[float] = 0.0204
STEP_S: Final[float] = 1.0 / 960.0
RESTITUTION_Z: Final[float] = 0.75
HORIZONTAL_RETENTION: Final[float] = 0.65
BALL_RADIUS_M: Final[float] = 0.0335
#: After a net collision the ball keeps this fraction of its horizontal
#: velocity, reversed in y (a dead drop on the hitter's side).
NET_RETENTION: Final[float] = 0.1
#: Below this rebound speed the ball stops bouncing and rolls.
REST_SPEED_Z_M_S: Final[float] = 0.5
#: Rolling deceleration on court, m/s^2 [assumption].
ROLLING_DECELERATION_M_S2: Final[float] = 0.6
_BISECTION_STEPS: Final[int] = 40


class FlightEventKind(StrEnum):
    BOUNCE = "bounce"
    NET = "net"


@dataclass(frozen=True)
class FlightEvent:
    kind: FlightEventKind
    time_s: float
    position_m: Vec3
    velocity_before_m_s: Vec3
    velocity_after_m_s: Vec3


@dataclass(frozen=True, eq=False)
class Trajectory:
    """Ball centre sampled on a uniform grid, plus exact contact events."""

    times_s: Float64Array
    positions_m: Float64Array
    events: tuple[FlightEvent, ...]

    @property
    def start_s(self) -> float:
        return float(self.times_s[0])

    @property
    def end_s(self) -> float:
        return float(self.times_s[-1])

    def position_at(self, times_s: npt.ArrayLike) -> Float64Array:
        """Linear interpolation on the 960 Hz grid; (N, 3) for N times.
        Times outside the trajectory are clamped to its ends."""
        t = np.atleast_1d(np.asarray(times_s, dtype=np.float64))
        return np.column_stack(
            [np.interp(t, self.times_s, self.positions_m[:, axis]) for axis in range(3)]
        )


def net_height_m(x_m: float) -> float:
    """Top of the net at ``x_m``: 0.914 at the centre strap, 1.07 at the posts,
    linear in between (the real cord sags; this is a stand-in)."""
    fraction = min(abs(x_m) / NET_POST_HALF_SPAN_M, 1.0)
    return NET_HEIGHT_CENTRE_M + fraction * (NET_HEIGHT_POST_M - NET_HEIGHT_CENTRE_M)


def acceleration(velocity: Vec3, drag_k: float) -> Vec3:
    vx, vy, vz = velocity
    drag = drag_k * math.sqrt(vx * vx + vy * vy + vz * vz)
    return (-drag * vx, -drag * vy, -GRAVITY_M_S2 - drag * vz)


def rk4_step(position: Vec3, velocity: Vec3, dt: float, drag_k: float) -> tuple[Vec3, Vec3]:
    px, py, pz = position
    vx, vy, vz = velocity
    a1 = acceleration(velocity, drag_k)
    v2 = (vx + 0.5 * dt * a1[0], vy + 0.5 * dt * a1[1], vz + 0.5 * dt * a1[2])
    a2 = acceleration(v2, drag_k)
    v3 = (vx + 0.5 * dt * a2[0], vy + 0.5 * dt * a2[1], vz + 0.5 * dt * a2[2])
    a3 = acceleration(v3, drag_k)
    v4 = (vx + dt * a3[0], vy + dt * a3[1], vz + dt * a3[2])
    a4 = acceleration(v4, drag_k)
    sixth = dt / 6.0
    new_position = (
        px + sixth * (vx + 2.0 * v2[0] + 2.0 * v3[0] + v4[0]),
        py + sixth * (vy + 2.0 * v2[1] + 2.0 * v3[1] + v4[1]),
        pz + sixth * (vz + 2.0 * v2[2] + 2.0 * v3[2] + v4[2]),
    )
    new_velocity = (
        vx + sixth * (a1[0] + 2.0 * a2[0] + 2.0 * a3[0] + a4[0]),
        vy + sixth * (a1[1] + 2.0 * a2[1] + 2.0 * a3[1] + a4[1]),
        vz + sixth * (a1[2] + 2.0 * a2[2] + 2.0 * a3[2] + a4[2]),
    )
    return new_position, new_velocity


def _roll_step(position: Vec3, velocity: Vec3, dt: float) -> tuple[Vec3, Vec3]:
    vx, vy, _ = velocity
    speed = math.hypot(vx, vy)
    if speed == 0.0:
        return (position[0], position[1], BALL_RADIUS_M), (0.0, 0.0, 0.0)
    scale = max(speed - ROLLING_DECELERATION_M_S2 * dt, 0.0) / speed
    nx, ny = vx * scale, vy * scale
    return (
        (position[0] + 0.5 * (vx + nx) * dt, position[1] + 0.5 * (vy + ny) * dt, BALL_RADIUS_M),
        (nx, ny, 0.0),
    )


def _bisect_fraction(
    position: Vec3, velocity: Vec3, dt: float, drag_k: float, crossed: Callable[[Vec3], bool]
) -> float:
    """Smallest fraction f of ``dt`` at which ``crossed(position)`` turns true."""
    low, high = 0.0, 1.0
    for _ in range(_BISECTION_STEPS):
        middle = 0.5 * (low + high)
        trial, _ = rk4_step(position, velocity, middle * dt, drag_k)
        if crossed(trial):
            high = middle
        else:
            low = middle
    return high


def simulate(
    position_m: npt.ArrayLike,
    velocity_m_s: npt.ArrayLike,
    start_s: float,
    duration_s: float,
    *,
    drag_k: float = DRAG_K_PER_M,
    stop_after_bounces: int | None = None,
    collide_net: bool = True,
) -> Trajectory:
    """Integrate a ball from a launch state for ``duration_s``.

    ``stop_after_bounces`` ends the trajectory at that bounce (its contact
    instant becomes the last sample), for aiming. ``collide_net=False`` lets
    the aimer solve a landing without the net in the way.
    """
    p0 = np.asarray(position_m, dtype=np.float64)
    v0 = np.asarray(velocity_m_s, dtype=np.float64)
    position: Vec3 = (float(p0[0]), float(p0[1]), float(p0[2]))
    velocity: Vec3 = (float(v0[0]), float(v0[1]), float(v0[2]))
    steps = int(round(duration_s / STEP_S))
    times = [start_s]
    positions = [position]
    events: list[FlightEvent] = []
    rolling = position[2] <= BALL_RADIUS_M and abs(velocity[2]) < REST_SPEED_Z_M_S
    bounces = 0
    for index in range(1, steps + 1):
        t0 = start_s + (index - 1) * STEP_S
        remaining = STEP_S
        while remaining > 0.0:
            if rolling:
                position, velocity = _roll_step(position, velocity, remaining)
                break
            trial_p, trial_v = rk4_step(position, velocity, remaining, drag_k)
            net_fraction = 2.0
            if collide_net and position[1] != 0.0 and (position[1] < 0.0) != (trial_p[1] < 0.0):
                start_side = position[1] < 0.0
                fraction = _bisect_fraction(
                    position, velocity, remaining, drag_k, lambda p: (p[1] < 0.0) != start_side
                )
                at_net, _ = rk4_step(position, velocity, fraction * remaining, drag_k)
                if (
                    abs(at_net[0]) <= NET_POST_HALF_SPAN_M
                    and at_net[2] < net_height_m(at_net[0]) + BALL_RADIUS_M
                ):
                    net_fraction = fraction
            ground_fraction = 2.0
            if trial_p[2] < BALL_RADIUS_M and trial_v[2] < 0.0:
                ground_fraction = _bisect_fraction(
                    position, velocity, remaining, drag_k, lambda p: p[2] < BALL_RADIUS_M
                )
            fraction = min(net_fraction, ground_fraction)
            if fraction > 1.0:
                position, velocity = trial_p, trial_v
                break
            dt_event = fraction * remaining
            contact, before = rk4_step(position, velocity, dt_event, drag_k)
            event_time = t0 + (STEP_S - remaining) + dt_event
            if ground_fraction <= net_fraction:
                position = (contact[0], contact[1], BALL_RADIUS_M)
                rebound = -RESTITUTION_Z * before[2]
                if rebound < REST_SPEED_Z_M_S:
                    rebound = 0.0
                    rolling = True
                after: Vec3 = (
                    HORIZONTAL_RETENTION * before[0],
                    HORIZONTAL_RETENTION * before[1],
                    rebound,
                )
                kind = FlightEventKind.BOUNCE
                bounces += 1
            else:
                position = (contact[0], -math.copysign(BALL_RADIUS_M, before[1]), contact[2])
                after = (NET_RETENTION * before[0], -NET_RETENTION * before[1], before[2])
                kind = FlightEventKind.NET
            events.append(FlightEvent(kind, event_time, position, before, after))
            velocity = after
            remaining -= dt_event
            if stop_after_bounces is not None and bounces >= stop_after_bounces:
                times.append(event_time)
                positions.append(position)
                return Trajectory(np.array(times), np.array(positions), tuple(events))
        times.append(start_s + index * STEP_S)
        positions.append(position)
    return Trajectory(np.array(times), np.array(positions), tuple(events))
