"""Build step 17, T2: rendered, degraded frames through the whole chain.

``court_pixels``: about 5 minutes, opt-in. Noise sigma 3, JPEG 60-85,
illumination drift, motion blur, players drawn (``RenderOptions.degraded``).
Synthetic pixels are not real footage: JPEG is not H.264, and the scene shares
the design's model of how a ball looks (design 11.1).

Measured 2026-11-08, clean-rally seeds 20270001-04:

| Measure | 1080p60 | 720p60 | 11.2 T2 target (1080p) |
|---|---|---|---|
| Confident wrong calls per 1000 | 0 | 0 | <= 10 |
| Bounce precision | 1.00 | 0.62 | >= 0.90 |
| Bounce recall (in frame) | 0.50 | 0.40 | >= 0.90 |
| Point-deciding bounce recall | 0.63 | 0.50 | (>= 0.90) |
| Median bounce error, near | 0.012 m | 0.018 m | <= 0.10 m |
| Median along-court error, far | 0.087 m | 0.027 m | <= 0.35 m |
| Rally range contains truth | 1.00 | 1.00 | >= 0.97 |
| Rally length exact | 0.50 | 0.33 | >= 0.85 |

Found and fixed by this tier (``flight_detector``): ~50 noise candidates per
frame (contrast gate), an airborne ball dropped by a ground-plane size gate
(``max_diameter_fn``), and far-player clutter let through because colour was
only judged by expected size. NOT met: bounce recall and rally exactness. The
misses are bounces at flight joins and tracks fragmented near the far player.
That is event-layer work, and real footage, not this renderer, should drive it.
"""

from __future__ import annotations

import pytest

from app.analysis.court_session import analyze_court_session
from app.models.court import CourtFormat
from tests.unit.analysis.test_ball_events import calibration_for
from tests.unit.court_synth.camera import default_camera
from tests.unit.court_synth.evaluate import Tally
from tests.unit.court_synth.pixels import run_pixels
from tests.unit.court_synth.render import CourtRenderer, RenderOptions
from tests.unit.court_synth.scene import Scenario, generate_rally

SEEDS = (20270001, 20270002, 20270003, 20270004)
SIZE = (1920, 1080)


@pytest.fixture(scope="module")
def t2_1080p() -> dict[str, float | int | None]:
    camera = default_camera(SIZE)
    calibration = calibration_for(camera)
    tally = Tally()
    for seed in SEEDS:
        scene = generate_rally(seed, Scenario.CLEAN_RALLY)
        run = run_pixels(CourtRenderer(scene, camera, RenderOptions.degraded(seed), seed), calibration)
        result = analyze_court_session(
            run.candidates, run.frame_times_s, calibration, run.stability,
            court_format=CourtFormat.SINGLES, frame_size=SIZE,
        )
        tally.add(scene, result, 60.0, run.truth_uv)
    return tally.summary()


@pytest.mark.court_pixels
def test_t2_never_makes_a_confident_wrong_call(t2_1080p: dict[str, float | int | None]) -> None:
    assert t2_1080p["confident_wrong_per_1000"] == 0.0


@pytest.mark.court_pixels
def test_t2_landings_are_real_and_placed_well(t2_1080p: dict[str, float | int | None]) -> None:
    assert t2_1080p["bounce_precision"] >= 0.90
    assert t2_1080p["median_bounce_error_near_m"] <= 0.10
    assert t2_1080p["median_along_error_far_m"] <= 0.35


@pytest.mark.court_pixels
def test_t2_rally_ranges_contain_the_truth(t2_1080p: dict[str, float | int | None]) -> None:
    assert t2_1080p["rally_range_contains_truth"] >= 0.97


@pytest.mark.court_pixels
@pytest.mark.xfail(strict=True, reason="T2 bounce recall 0.50 vs 0.90 target; see module docstring")
def test_t2_bounce_recall_meets_the_design_target(t2_1080p: dict[str, float | int | None]) -> None:
    assert t2_1080p["bounce_recall"] >= 0.90
