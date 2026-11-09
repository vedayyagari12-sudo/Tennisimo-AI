"""Build step 17, T0: the whole court chain against scene truth (design 11.2).

T0 projects truth to candidate records with pixel noise; no pixels are drawn.
Synthetic results prove the logic and propagate known truth, nothing more:
real footage is still required for any accuracy claim (design 11.4).

Measured 2026-11-08 on 100 distinct seeds (``test_t0_full_run``), every
scenario except double fault (``t0_records`` projects only the first ball, so
a second serve never exists in the candidates):

    bounce precision 0.998, point-deciding bounce recall 0.993,
    all-bounce recall 0.919 (misses are dead-ball bounces after the point),
    near median error 0.008 m, far median along-court error 0.029 m,
    in/out accuracy outside the band 0.996, confident wrong 0 per 1000,
    band coverage 0.996, rally exact 0.99 / within one 0.99 / range 1.00,
    point suggestions 51 made, 0 wrong, 38 sent to review.
"""

from __future__ import annotations

import pytest

from app.analysis.camera_stability import CameraStability
from app.analysis.court_session import analyze_court_session
from app.analysis.match_score import ScoreConfig
from app.analysis.point_inference import events_from_rallies
from app.models.court import CourtEnd, CourtFormat, PlayerId, StabilityVerdict
from tests.unit.analysis.test_ball_events import CALIBRATION, CAMERA
from tests.unit.analysis.test_flight_track import t0_records
from tests.unit.court_synth.evaluate import Tally
from tests.unit.court_synth.scene import Scenario, generate_rally
from tests.unit.court_synth.truth import THRESHOLDS_T0

STABLE = CameraStability(StabilityVerdict.STABLE, 0.0005, 0.3, None, 20)
SCENARIOS = [s for s in Scenario if s is not Scenario.DOUBLE_FAULT]
CONFIG = ScoreConfig(best_of_sets=3, ad_scoring=True)


def run(count: int, first_seed: int = 20270000) -> tuple[Tally, int, int]:
    tally = Tally()
    made = wrong = 0
    for index in range(count):
        seed = first_seed + index
        scene = generate_rally(seed, SCENARIOS[index % len(SCENARIOS)])
        candidates, times, truth_uv = t0_records(scene, CAMERA, seed)
        result = analyze_court_session(
            candidates, times, CALIBRATION, STABLE,
            court_format=CourtFormat.SINGLES, frame_size=CAMERA.frame_size,
        )
        tally.add(scene, result, 60.0, truth_uv)
        events = events_from_rallies(result.rallies, CONFIG)
        if events and not events[0].needs_review and events[0].winner is not None:
            made += 1
            end = CourtEnd.NEAR if events[0].winner is PlayerId.A else CourtEnd.FAR
            truth = scene.rallies[0].point_winner_end
            wrong += int(truth is not None and end is not truth)
    return tally, made, wrong


def check(
    summary: dict[str, float | int | None], made: int, wrong: int, *, rally_rates: bool = True
) -> None:
    t = THRESHOLDS_T0
    assert summary["bounce_precision"] >= t["bounce_precision_min"]
    # Gated on the bounces that decide a call or a point. All-bounce recall
    # (dead-ball bounces after the point) is reported, not gated.
    assert summary["deciding_bounce_recall"] >= t["bounce_recall_min"]
    assert summary["median_bounce_error_near_m"] <= t["median_bounce_error_near_max_m"]
    assert summary["median_along_error_far_m"] <= t["median_along_error_far_max_m"]
    assert summary["in_out_accuracy_outside_band"] >= t["in_out_accuracy_outside_band_min"]
    assert summary["confident_wrong_per_1000"] <= t["confident_wrong_per_1000_max"]
    assert summary["rally_range_contains_truth"] >= t["rally_range_contains_truth_min"]
    if rally_rates:
        # One rally in twenty is 5 %: the exact and within-one rates are only
        # meaningful on the full run.
        assert summary["rally_within_one"] >= t["rally_length_within_one_min"]
        assert summary["rally_exact"] >= t["rally_length_exact_min"]
    # Coverage is gated only from below here: SIGMA_INFLATION is set from T2.
    assert summary["band_coverage"] >= t["band_coverage_range"][0]  # type: ignore[index]
    assert made == 0 or wrong / made <= t["point_suggestion_wrong_max"]
    assert summary["rally_count_mismatch_scenes"] == 0


def test_t0_smoke() -> None:
    """Twenty distinct scenes, every scenario twice: the fast gate."""
    tally, made, wrong = run(20)
    check(tally.summary(), made, wrong, rally_rates=False)


@pytest.mark.slow
def test_t0_full_run() -> None:
    """The 100-seed run the module docstring reports."""
    tally, made, wrong = run(100)
    summary = tally.summary()
    print(summary, {"point_suggestions": made, "wrong": wrong})
    check(summary, made, wrong)
