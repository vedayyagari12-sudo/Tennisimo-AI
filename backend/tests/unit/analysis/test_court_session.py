"""Build step 16: ``analysis/court_session.py``.

Done when: refuses on ``moving``; emits no call without enough taps; landings
from synthetic rallies sit on the true bounces. Synthetic scenes prove the
plumbing, not the accuracy on real footage.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from app.analysis.ball_events import PoseHit
from app.analysis.camera_stability import CameraStability
from app.analysis.court_calibration import CourtTap, calibrate_court
from app.analysis.court_model import keypoint_xy
from app.analysis.court_session import (
    COURT_ENGINE_VERSION,
    MIN_FPS,
    CourtSessionResult,
    analyze_court_session,
    median_fps,
)
from app.analysis.flight_track import FLIGHT_CANDIDATE_DTYPE
from app.models.court import (
    CourtEnd,
    CourtFormat,
    CourtRefusalReason,
    CourtSessionStatus,
    LineCallTarget,
    LineVerdict,
    StabilityVerdict,
)
from tests.unit.analysis.synthetic_court import RECOMMENDED_SIX
from tests.unit.analysis.test_ball_events import CALIBRATION, CAMERA
from tests.unit.analysis.test_flight_track import t0_records
from tests.unit.court_synth.scene import SceneTruth, TruthBounce, generate_rally

SEEDS = range(20261102, 20261108)
STABLE = CameraStability(StabilityVerdict.STABLE, 0.0005, 0.3, None, 20)
MATCH_S = 0.05


def run(
    seed: int,
    *,
    stability: CameraStability = STABLE,
    calibration=CALIBRATION,  # noqa: ANN001 - CourtCalibration | None
    pose_hits: tuple[PoseHit, ...] = (),
    frame_size: tuple[int, int] = CAMERA.frame_size,
) -> tuple[SceneTruth, CourtSessionResult]:
    scene = generate_rally(seed)
    candidates, times, _ = t0_records(scene, CAMERA, seed)
    result = analyze_court_session(
        candidates,
        times,
        calibration,
        stability,
        pose_hits,
        court_format=CourtFormat.SINGLES,
        frame_size=frame_size,
    )
    return scene, result


def matched(scene: SceneTruth, result: CourtSessionResult) -> list[tuple[TruthBounce, object]]:
    pairs = []
    for bounce in scene.bounces:
        if not bounce.visible:
            continue
        close = [l for l in result.landings if abs(l.bounce.time_s - bounce.time_s) <= MATCH_S]
        if close:
            pairs.append((bounce, close[0]))
    return pairs


# ------------------------------------------------------------- landings


@pytest.mark.parametrize("seed", SEEDS)
def test_landings_sit_on_the_true_bounces(seed: int) -> None:
    scene, result = run(seed)
    assert result.status is CourtSessionStatus.COMPLETE
    pairs = matched(scene, result)
    visible = [b for b in scene.bounces if b.visible]
    assert len(pairs) >= len(visible) - 2
    errors = [
        float(np.hypot(l.bounce.court_xy_m[0] - b.position_m[0], l.bounce.court_xy_m[1] - b.position_m[1]))
        for b, l in pairs
    ]
    assert float(np.median(errors)) < 0.15


@pytest.mark.parametrize("seed", SEEDS)
def test_no_confident_wrong_call_against_the_full_half(seed: int) -> None:
    scene, result = run(seed)
    for bounce, landing in matched(scene, result):
        call = landing.call
        if call is None or call.verdict is LineVerdict.TOO_CLOSE_TO_CALL:
            continue
        if bounce.target is not LineCallTarget.SINGLES_HALF:
            continue  # serves are re-called against the box in point_inference
        if landing.hitter_end is not bounce.hitter_end:
            continue
        assert (call.verdict is LineVerdict.IN) == bounce.is_in


def test_a_bounce_with_no_known_hitter_is_called_against_its_own_half() -> None:
    _, result = run(SEEDS[0])
    unknown = [l for l in result.landings if l.hitter_end is None]
    assert unknown, "the serve bounce has no preceding hit without pose"
    for landing in unknown:
        assert landing.call is not None
        assert landing.call.target is LineCallTarget.HALF_ONLY


def test_known_hitter_is_called_against_the_other_half() -> None:
    _, result = run(SEEDS[0])
    known = [l for l in result.landings if l.hitter_end is not None]
    assert known
    for landing in known:
        assert landing.bounce.half is not landing.hitter_end
        assert landing.call is not None and landing.call.target is LineCallTarget.SINGLES_HALF


@pytest.mark.parametrize("seed", SEEDS)
def test_a_bounce_is_never_called_in_on_the_hitters_own_side(seed: int) -> None:
    """Regression: a missed opponent hit left the bounce credited to the
    earlier hitter, and the far-half region (no net edge) then called a ball
    deep in the near half IN."""
    _, result = run(seed)
    for landing in result.landings:
        if landing.call is not None and landing.call.verdict is LineVerdict.IN:
            assert landing.hitter_end is not landing.bounce.half


def test_pose_hits_mark_the_serve_landing() -> None:
    scene = generate_rally(SEEDS[0])
    rally = scene.rallies[0]
    pose = tuple(
        PoseHit(h.time_s, None, h.end, 0.9, "serve" if h.is_serve else "forehand") for h in rally.hits
    )
    _, result = run(SEEDS[0], pose_hits=pose)
    first = result.landings[0]
    assert first.is_serve
    assert first.hitter_end is rally.hits[0].end
    assert not any(l.is_serve for l in result.landings[1:])


def test_rallies_are_found() -> None:
    scene, result = run(SEEDS[2])
    assert len(result.rallies) == len(scene.rallies)
    for rally in result.rallies:
        assert rally.length_min <= rally.length_hits <= rally.length_max


def test_summary_counts_every_landing() -> None:
    _, result = run(SEEDS[0])
    assert result.summary.count == len(result.landings)
    assert sum(result.summary.width_counts.values()) == len(result.landings)


# ------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        (StabilityVerdict.MOVING, CourtRefusalReason.CAMERA_MOVING),
        (StabilityVerdict.UNKNOWN, CourtRefusalReason.CAMERA_UNKNOWN),
    ],
)
def test_refuses_an_unfixed_camera(verdict: StabilityVerdict, reason: CourtRefusalReason) -> None:
    _, result = run(SEEDS[0], stability=CameraStability(verdict, None, None, None, 20))
    assert result.status is CourtSessionStatus.REFUSED
    assert result.refusal_reason is reason
    assert result.landings == () and result.rallies == ()
    assert result.summary.count == 0 and result.summary.median_sigma_along_m is None


def test_shaky_camera_is_analysed() -> None:
    shaky = CameraStability(StabilityVerdict.SHAKY, 0.002, 2.0, None, 20)
    _, result = run(SEEDS[0], stability=shaky)
    assert result.status is CourtSessionStatus.COMPLETE
    assert result.landings


def test_refuses_without_calibration() -> None:
    _, result = run(SEEDS[0], calibration=None)
    assert result.refusal_reason is CourtRefusalReason.NOT_CALIBRATED


def test_calibration_for_another_frame_size_is_not_used() -> None:
    _, result = run(SEEDS[0], frame_size=(1280, 720))
    assert result.refusal_reason is CourtRefusalReason.NOT_CALIBRATED


def test_refuses_a_low_frame_rate() -> None:
    times = np.arange(100, dtype=np.float64) / 20.0
    empty = np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)
    result = analyze_court_session(
        empty, times, CALIBRATION, STABLE, court_format=CourtFormat.SINGLES,
        frame_size=CAMERA.frame_size,
    )
    assert result.refusal_reason is CourtRefusalReason.FRAME_RATE_TOO_LOW
    assert result.fps == pytest.approx(20.0)
    assert MIN_FPS == 24.0


def test_median_fps() -> None:
    assert median_fps(np.arange(10) / 60.0) == pytest.approx(60.0)
    assert median_fps(np.array([0.0])) == 0.0


# ------------------------------------------------------- partial and taps


def test_bumped_camera_is_truncated_and_partial() -> None:
    scene, full = run(SEEDS[0])
    bump = float(np.median([l.bounce.time_s for l in full.landings]))
    bumped = CameraStability(StabilityVerdict.STABLE, 0.0005, 0.3, bump, 20)
    _, result = run(SEEDS[0], stability=bumped)
    assert result.status is CourtSessionStatus.PARTIAL
    assert result.analysed_until_s == pytest.approx(bump)
    assert result.landings
    assert all(l.bounce.time_s < bump for l in result.landings)
    assert len(result.landings) < len(full.landings)


def test_fewer_than_eight_taps_gives_landings_but_no_calls() -> None:
    uv = CAMERA.project(np.array([[*keypoint_xy(k), 0.0] for k in RECOMMENDED_SIX]))
    taps = [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(RECOMMENDED_SIX, uv, strict=True)]
    six = calibrate_court(taps, CAMERA.frame_size, sigma_tap_px=1.0).calibration
    assert six is not None and not six.calls_allowed
    _, result = run(SEEDS[0], calibration=six)
    assert result.status is CourtSessionStatus.COMPLETE
    assert result.landings
    assert all(l.call is None for l in result.landings)
    assert result.summary.verdict_counts[LineVerdict.IN] == 0


def test_no_candidates_gives_an_empty_complete_session() -> None:
    times = np.arange(600, dtype=np.float64) / 60.0
    empty: npt.NDArray[np.void] = np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)
    result = analyze_court_session(
        empty, times, CALIBRATION, STABLE, court_format=CourtFormat.SINGLES,
        frame_size=CAMERA.frame_size,
    )
    assert result.status is CourtSessionStatus.COMPLETE
    assert result.landings == () and result.rallies == ()


# ---------------------------------------------------------- determinism


def test_same_input_gives_identical_output() -> None:
    _, a = run(SEEDS[1])
    _, b = run(SEEDS[1])
    assert [l.bounce.court_xy_m for l in a.landings] == [l.bounce.court_xy_m for l in b.landings]
    assert [l.call.verdict if l.call else None for l in a.landings] == [
        l.call.verdict if l.call else None for l in b.landings
    ]
    assert [r.rally_id for r in a.rallies] == [r.rally_id for r in b.rallies]
    assert a.versions["court_engine"] == COURT_ENGINE_VERSION


def test_hitter_end_is_only_near_or_far() -> None:
    _, result = run(SEEDS[3])
    assert {l.hitter_end for l in result.landings} <= {CourtEnd.NEAR, CourtEnd.FAR, None}
