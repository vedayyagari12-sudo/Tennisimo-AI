"""Court session orchestrator (SESSION_MODE_PHASE2.md build step 16). PURE.

Flight candidates in, landings, line calls and rallies out:

    candidates -> link_flights -> detect_ball_events -> group_event_runs
      -> per bounce: bounce_to_court, target region, call_bounce, classify_landing
      -> segment_rallies -> summarize_shot_map

Refusals (brief G7), checked in this order, each with an EMPTY result rather
than a guess:
* ``frame_rate_too_low``: under 24 fps the ball moves too far between frames.
* ``camera_moving`` / ``camera_unknown``: the court-to-image mapping is only
  valid for a fixed camera, and an unknown camera cannot be assumed fixed.
* ``not_calibrated``: without court taps there is no ground position at all.

A camera bumped part-way is truncated at ``bumped_at_s`` and the result is
``partial``: everything after the bump would be mapped with a stale homography.

Line calls are made only when the calibration allows them (brief G21: at least
8 accepted taps). Otherwise each landing carries ``call=None``, which the wire
shows as "not measured" -- never as in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import numpy as np
import numpy.typing as npt

from app.analysis.ball_events import BALL_EVENTS_VERSION, BallEvent, PoseHit, detect_ball_events
from app.analysis.camera_stability import CameraStability
from app.analysis.court_calibration import CALIBRATION_VERSION, CourtCalibration
from app.analysis.court_model import COURT_MODEL_VERSION, half_region
from app.analysis.flight_track import BallFlight, link_flights
from app.analysis.homography import image_scale_px_per_m
from app.analysis.line_calls import LINE_CALLS_VERSION, bounce_to_court, call_bounce
from app.analysis.placement import (
    PLACEMENT_VERSION,
    ShotLanding,
    ShotMapSummary,
    classify_landing,
    summarize_shot_map,
)
from app.analysis.rally import RALLY_VERSION, Rally, group_event_runs, preceding_hits, segment_rallies
from app.models.court import (
    BallEventKind,
    CourtEnd,
    CourtFormat,
    CourtRefusalReason,
    CourtSessionStatus,
    LineCallTarget,
    StabilityVerdict,
)

Float64Array = npt.NDArray[np.float64]

COURT_ENGINE_VERSION: Final[str] = "court_engine_v1"
MIN_FPS: Final[float] = 24.0
#: The shot type a pose hit reports for a serve (``ShotType.SERVE``).
SERVE_SHOT_TYPE: Final[str] = "serve"


@dataclass(frozen=True, eq=False)
class CourtSessionResult:
    status: CourtSessionStatus
    refusal_reason: CourtRefusalReason | None
    calibration: CourtCalibration | None
    stability: CameraStability
    fps: float
    #: Seconds of video actually analysed (shorter than the input when bumped).
    analysed_until_s: float | None
    flights: tuple[BallFlight, ...]
    events: tuple[BallEvent, ...]
    landings: tuple[ShotLanding, ...]
    rallies: tuple[Rally, ...]
    summary: ShotMapSummary
    versions: Mapping[str, str]


def _versions() -> Mapping[str, str]:
    return MappingProxyType(
        {
            "court_engine": COURT_ENGINE_VERSION,
            "court_model": COURT_MODEL_VERSION,
            "calibration": CALIBRATION_VERSION,
            "ball_events": BALL_EVENTS_VERSION,
            "line_calls": LINE_CALLS_VERSION,
            "placement": PLACEMENT_VERSION,
            "rally": RALLY_VERSION,
        }
    )


def median_fps(frame_times_s: Float64Array) -> float:
    """1 / median PTS delta. 0.0 with fewer than two frames."""
    times = np.asarray(frame_times_s, dtype=np.float64)
    if times.size < 2:
        return 0.0
    step = float(np.median(np.diff(times)))
    return 1.0 / step if step > 0.0 else 0.0


def refusal_for(
    fps: float, stability: CameraStability, calibration: CourtCalibration | None
) -> CourtRefusalReason | None:
    if fps < MIN_FPS:
        return CourtRefusalReason.FRAME_RATE_TOO_LOW
    if stability.verdict is StabilityVerdict.MOVING:
        return CourtRefusalReason.CAMERA_MOVING
    if stability.verdict is StabilityVerdict.UNKNOWN:
        return CourtRefusalReason.CAMERA_UNKNOWN
    if calibration is None:
        return CourtRefusalReason.NOT_CALIBRATED
    return None


def _other(end: CourtEnd) -> CourtEnd:
    return CourtEnd.FAR if end is CourtEnd.NEAR else CourtEnd.NEAR


def _full_half_target(court_format: CourtFormat) -> LineCallTarget:
    return (
        LineCallTarget.SINGLES_HALF
        if court_format is CourtFormat.SINGLES
        else LineCallTarget.DOUBLES_HALF
    )


def _net_between(run: Sequence[BallEvent], hit: BallEvent, bounce: BallEvent) -> bool:
    return any(
        e.kind is BallEventKind.NET_CONTACT and hit.time_s < e.time_s < bounce.time_s
        for e in run
    )


def build_landings(
    events: Sequence[BallEvent],
    flights: Sequence[BallFlight],
    calibration: CourtCalibration,
    stability: CameraStability,
    *,
    court_format: CourtFormat,
) -> tuple[ShotLanding, ...]:
    """One landing per BOUNCE, in time order (brief G10).

    The target is the half OPPOSITE the hitter, from the last HIT before the
    bounce in its run of activity. With no such hit the hitter is unknown and
    the bounce is only called against the half it landed in (``half_only``):
    it can be called out past a baseline or sideline, never "into the net".
    A bounce on the hitter's own side is never called against the far half
    (see the body). Match-mode service boxes are re-called in
    ``point_inference``.
    """
    landings: list[ShotLanding] = []
    for run in group_event_runs(events, flights=flights):
        hitters = preceding_hits(run)
        for event in run:
            if event.kind is not BallEventKind.BOUNCE:
                continue
            bounce = bounce_to_court(event, calibration, stability)
            hit = hitters.get(event.event_id)
            hitter_end = hit.end if hit is not None else None
            shot_type = hit.shot_type if hit is not None else None
            net_first = hit is not None and _net_between(run, hit, event)
            if hitter_end is bounce.half and not net_first:
                # A bounce on the hitter's OWN side with no net contact means
                # the opponent's hit in between was missed: the attribution is
                # wrong, not the bounce. Calling it against the far half would
                # say IN, because a half has no net edge to be out across.
                hit, hitter_end, shot_type = None, None, None
            if hitter_end is not None:
                region = half_region(_other(hitter_end), court_format)
                target = _full_half_target(court_format)
            else:
                region = half_region(bounce.half, court_format)
                target = LineCallTarget.HALF_ONLY
            if hitter_end is bounce.half:
                # Net contact, then the ball fell back: it never crossed, and
                # no line decides that. Not measured as in/out; the rally's
                # end reason carries the net.
                call = None
            else:
                call = call_bounce(bounce, region, calibration, target=target)
            landings.append(
                classify_landing(
                    bounce,
                    call,
                    hitter_end,
                    shot_type,
                    shot_type == SERVE_SHOT_TYPE,
                    target=target,
                )
            )
    return tuple(sorted(landings, key=lambda landing: (landing.bounce.time_s, landing.bounce.event_id)))


def _refused(
    reason: CourtRefusalReason,
    calibration: CourtCalibration | None,
    stability: CameraStability,
    fps: float,
) -> CourtSessionResult:
    return CourtSessionResult(
        status=CourtSessionStatus.REFUSED,
        refusal_reason=reason,
        calibration=calibration,
        stability=stability,
        fps=fps,
        analysed_until_s=None,
        flights=(),
        events=(),
        landings=(),
        rallies=(),
        summary=summarize_shot_map(()),
        versions=_versions(),
    )


def analyze_court_session(
    candidates: npt.NDArray[np.void],
    frame_times_s: Float64Array,
    calibration: CourtCalibration | None,
    stability: CameraStability,
    pose_hits: Sequence[PoseHit] = (),
    *,
    court_format: CourtFormat,
    frame_size: tuple[int, int],
) -> CourtSessionResult:
    """Brief G7. Deterministic: the same candidates give the same result.

    ``frame_size`` is the TRACK_SPACE size the candidates and the calibration
    share; a calibration made for another size is refused as not calibrated
    rather than mapped with the wrong pixels.
    """
    times = np.asarray(frame_times_s, dtype=np.float64)
    fps = median_fps(times)
    if calibration is not None and tuple(calibration.frame_size) != tuple(frame_size):
        calibration = None
    reason = refusal_for(fps, stability, calibration)
    if reason is not None:
        return _refused(reason, calibration, stability, fps)
    assert calibration is not None  # refusal_for returned None

    status = CourtSessionStatus.COMPLETE
    analysed_until = float(times[-1]) if times.size else None
    if stability.bumped_at_s is not None:
        keep_frames = int(np.searchsorted(times, float(stability.bumped_at_s), side="left"))
        times = times[:keep_frames]
        candidates = candidates[np.asarray(candidates["frame_index"]) < keep_frames]
        pose_hits = tuple(h for h in pose_hits if h.time_s < float(stability.bumped_at_s))
        status = CourtSessionStatus.PARTIAL
        analysed_until = float(stability.bumped_at_s)

    def scale(x: float, y: float) -> float:
        return image_scale_px_per_m(calibration.pose, calibration.h_image_to_court, (x, y))

    flights = link_flights(candidates, times, scale) if times.size else ()
    events = detect_ball_events(flights, calibration, pose_hits)
    landings = build_landings(events, flights, calibration, stability, court_format=court_format)
    rallies = segment_rallies(events, landings, flights=flights)
    return CourtSessionResult(
        status=status,
        refusal_reason=None,
        calibration=calibration,
        stability=stability,
        fps=fps,
        analysed_until_s=analysed_until,
        flights=tuple(flights),
        events=events,
        landings=landings,
        rallies=rallies,
        summary=summarize_shot_map(landings),
        versions=_versions(),
    )
