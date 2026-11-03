"""Build step 11: ``analysis/line_calls.py`` (design 6.3).

Done when: never confident inside the band (a seeded-sweep property test);
boundary cases at exactly +/- r; a ball touching a line is in; the far edge
of the box centre line; a weak bounce forces too close; covariance grows with
distance. Plus brief G21: no call unless the calibration allows calls.
"""

from __future__ import annotations

import math
from types import MappingProxyType

import numpy as np
import pytest

from app.analysis.ball_events import ONE_SIDED_BOUNCE_FLAG, WEAK_BOUNCE_FLAG, BallEvent
from app.analysis.camera_stability import CameraStability
from app.analysis.court_calibration import CourtCalibration, CourtTap, calibrate_court
from app.analysis.court_model import (
    CourtEnd,
    CourtFormat,
    CourtKeypoint,
    CourtLine,
    CourtRegion,
    half_region,
    keypoint_xy,
    service_box_region,
)
from app.analysis.line_calls import (
    BALL_CONTACT_PATCH_RADIUS_M,
    LINE_CALLS_VERSION,
    BouncePoint,
    bounce_to_court,
    call_bounce,
    call_region,
    uncertainty_ellipse,
)
from app.models.court import BallEventKind, LineCallTarget, LineVerdict, StabilityVerdict
from tests.unit.analysis.synthetic_court import RECOMMENDED_SIX
from tests.unit.court_synth.camera import default_camera

CAMERA = default_camera((1920, 1080))
EIGHT = (*RECOMMENDED_SIX, CourtKeypoint.NEAR_LEFT_SERVICE_CORNER, CourtKeypoint.NEAR_RIGHT_SERVICE_CORNER)
STILL = CameraStability(StabilityVerdict.STABLE, 0.0, 0.0, None, 20)
TARGET = LineCallTarget.SINGLES_HALF


def calibration(keypoints: tuple[CourtKeypoint, ...] = EIGHT) -> CourtCalibration:
    uv = CAMERA.project(np.array([[*keypoint_xy(k), 0.0] for k in keypoints]))
    taps = [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(keypoints, uv, strict=True)]
    result = calibrate_court(taps, CAMERA.frame_size, sigma_tap_px=1.0).calibration
    assert result is not None
    return result


CALIBRATION = calibration()


def bounce_event(x: float, y: float, *, kind: BallEventKind = BallEventKind.BOUNCE,
                 cov_px2: float = 1.0, confidence: float = 0.9, flags: tuple[str, ...] = ()) -> BallEvent:
    """A bounce whose ball CENTRE (radius above the ground) is at (x, y)."""
    u, v = CAMERA.project(np.array([[x, y, 0.0335]]))[0]
    return BallEvent("b_000001", 0, kind, 3.0, 0.004, (float(u), float(v)), np.eye(2) * cov_px2,
                     (0.0, 300.0), (0.0, -300.0), confidence, flags)


def point(x: float, y: float, sigma: float | tuple[float, float] = 0.05, *, confidence: float = 0.9,
          flags: tuple[str, ...] = ()) -> BouncePoint:
    sx, sy = (sigma, sigma) if isinstance(sigma, float) else sigma
    return BouncePoint("b_000001", 3.0, 0.004, (x, y), np.diag([sx * sx, sy * sy]),
                       CourtEnd.NEAR if y < 0 else CourtEnd.FAR, confidence, flags)


# ------------------------------------------------------------ to the ground


def test_bounce_maps_exactly_to_its_contact_point() -> None:
    for x, y in ((1.2, -8.0), (-3.0, 10.5), (0.0, 2.0)):
        bounce = bounce_to_court(bounce_event(x, y), CALIBRATION, STILL)
        assert bounce.court_xy_m == pytest.approx((x, y), abs=1e-6)
        assert bounce.half is (CourtEnd.NEAR if y < 0 else CourtEnd.FAR)
        assert bounce.time_sigma_s == 0.004 and bounce.event_id == "b_000001"


def test_only_bounces_map_to_the_ground() -> None:
    for kind in (BallEventKind.HIT, BallEventKind.NET_CONTACT, BallEventKind.UNKNOWN_KINK):
        with pytest.raises(ValueError):
            bounce_to_court(bounce_event(1.0, 5.0, kind=kind), CALIBRATION, STILL)


def test_covariance_grows_with_distance() -> None:
    sigmas = []
    for y in (-10.0, -5.0, 0.0, 5.0, 10.0):
        cov = bounce_to_court(bounce_event(0.5, y), CALIBRATION, STILL).cov_m2
        assert np.allclose(cov, cov.T) and np.all(np.linalg.eigvalsh(cov) > 0.0)
        sigmas.append(math.sqrt(cov[1, 1]))
    assert sigmas == sorted(sigmas)
    assert sigmas[-1] > 5.0 * sigmas[0]


def test_shake_and_tap_error_add_to_the_covariance() -> None:
    event = bounce_event(1.0, 8.0)
    still = bounce_to_court(event, CALIBRATION, STILL).cov_m2
    shaky = bounce_to_court(event, CALIBRATION, CameraStability(StabilityVerdict.SHAKY, 0.002, 2.0, None, 20)).cov_m2
    assert np.trace(shaky) > np.trace(still)
    unknown = CameraStability(StabilityVerdict.UNKNOWN, None, None, None, 0)
    assert np.array_equal(bounce_to_court(event, CALIBRATION, unknown).cov_m2, still)
    no_fit = bounce_to_court(bounce_event(1.0, 8.0, cov_px2=0.0), CALIBRATION, STILL).cov_m2
    assert np.trace(no_fit) > 0.0  # tap error alone


# ------------------------------------------------------------------ ellipse


def test_ellipse_axes_and_angle() -> None:
    assert uncertainty_ellipse(np.diag([4.0, 1.0]), z=1.0) == pytest.approx((2.0, 1.0, 0.0))
    assert uncertainty_ellipse(np.diag([1.0, 4.0]), z=1.0) == pytest.approx((2.0, 1.0, 90.0))
    theta = math.radians(30.0)
    rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    major, minor, angle = uncertainty_ellipse(rotation @ np.diag([9.0, 1.0]) @ rotation.T, z=2.0)
    assert (major, minor, angle) == pytest.approx((6.0, 2.0, 30.0))
    _, _, flipped = uncertainty_ellipse(rotation.T @ np.diag([9.0, 1.0]) @ rotation, z=1.0)
    assert flipped == pytest.approx(150.0) and 0.0 <= flipped < 180.0


# -------------------------------------------------------------------- calls


def test_clear_in_and_clear_out() -> None:
    region = half_region(CourtEnd.FAR, CourtFormat.SINGLES)
    inside = call_region(point(0.0, 6.0, 0.05), region, target=TARGET)
    assert inside.verdict is LineVerdict.IN and inside.version == LINE_CALLS_VERSION
    assert inside.target is TARGET
    long = call_region(point(0.0, 12.6, 0.05), region, target=TARGET)
    assert long.verdict is LineVerdict.OUT and long.deciding_line is CourtLine.FAR_BASELINE
    assert long.signed_margin_m == pytest.approx(11.885 - 12.6 + BALL_CONTACT_PATCH_RADIUS_M)
    wide = call_region(point(-4.6, 6.0, 0.05), region, target=TARGET)
    assert wide.verdict is LineVerdict.OUT and wide.deciding_line is CourtLine.LEFT_SINGLES_SIDELINE


def test_ball_touching_a_line_is_in() -> None:
    region = half_region(CourtEnd.NEAR, CourtFormat.SINGLES)
    touching = call_region(point(4.115 + 0.015, -6.0, 0.001), region, target=TARGET)
    assert touching.verdict is LineVerdict.IN
    assert touching.deciding_line is CourtLine.RIGHT_SINGLES_SIDELINE
    assert touching.signed_margin_m == pytest.approx(0.005)
    clear_of_it = call_region(point(4.115 + 0.025, -6.0, 0.001), region, target=TARGET)
    assert clear_of_it.verdict is LineVerdict.OUT


def test_far_edge_of_the_box_centre_line() -> None:
    box = service_box_region(CourtEnd.NEAR, "deuce")  # x in [-0.025, 4.115]
    on_the_line = call_region(point(-0.03, -3.0, 0.002), box, target=LineCallTarget.SERVICE_BOX_DEUCE)
    assert on_the_line.verdict is LineVerdict.IN
    beyond = call_region(point(-0.06, -3.0, 0.002), box, target=LineCallTarget.SERVICE_BOX_DEUCE)
    assert beyond.verdict is LineVerdict.OUT and beyond.deciding_line is CourtLine.CENTRE_SERVICE_LINE
    assert beyond.signed_margin_m == pytest.approx(-0.015)


def test_exactly_at_plus_and_minus_r() -> None:
    region = CourtRegion("test", -10.0, 0.105, -10.0, 10.0,
                         MappingProxyType({"x_min": CourtLine.LEFT_DOUBLES_SIDELINE,
                                           "x_max": CourtLine.RIGHT_SINGLES_SIDELINE,
                                           "y_min": CourtLine.NEAR_BASELINE,
                                           "y_max": CourtLine.FAR_BASELINE}))
    # margin = 0.105 - 0.0 + 0.02 = 0.125 = r = 0.5 x 0.25 exactly.
    at_plus = call_region(point(0.0, 0.0, 0.25), region, target=TARGET, z=0.5)
    assert at_plus.signed_margin_m == 0.125 and at_plus.uncertainty_radius_m == 0.125
    assert at_plus.verdict is LineVerdict.IN
    # Beyond the line: z chosen so r = z x 0.25 equals |margin| exactly
    # (sigma a power of two makes the product exact).
    margin = (region.x_max - 0.25) + BALL_CONTACT_PATCH_RADIUS_M
    at_minus = call_region(point(0.25, 0.0, 0.25), region, target=TARGET, z=-margin / 0.25)
    assert at_minus.signed_margin_m == margin == -at_minus.uncertainty_radius_m
    assert at_minus.verdict is LineVerdict.OUT
    just_inside_band = call_region(point(0.0, 0.0, 0.2500001), region, target=TARGET, z=0.5)
    assert just_inside_band.verdict is LineVerdict.TOO_CLOSE_TO_CALL


def test_weak_bounce_forces_too_close() -> None:
    region = half_region(CourtEnd.FAR, CourtFormat.SINGLES)
    weak = call_region(point(0.0, 6.0, 0.05, confidence=0.3), region, target=TARGET)
    assert weak.verdict is LineVerdict.TOO_CLOSE_TO_CALL and WEAK_BOUNCE_FLAG in weak.flags
    flagged = call_region(point(0.0, 6.0, 0.05, flags=(WEAK_BOUNCE_FLAG,)), region, target=TARGET)
    assert flagged.verdict is LineVerdict.TOO_CLOSE_TO_CALL


def test_one_sided_forces_too_close_only_across_the_court() -> None:
    region = half_region(CourtEnd.FAR, CourtFormat.SINGLES)
    near_baseline = call_region(point(0.0, 11.0, 0.05, flags=(ONE_SIDED_BOUNCE_FLAG,)), region, target=TARGET)
    assert near_baseline.deciding_line is CourtLine.FAR_BASELINE
    assert near_baseline.verdict is LineVerdict.TOO_CLOSE_TO_CALL
    near_sideline = call_region(point(3.6, 6.0, 0.05, flags=(ONE_SIDED_BOUNCE_FLAG,)), region, target=TARGET)
    assert near_sideline.deciding_line is CourtLine.RIGHT_SINGLES_SIDELINE
    assert near_sideline.verdict is LineVerdict.IN


# --------------------------------------------------- property: the band


def test_never_confident_inside_the_band_seeded_sweep() -> None:
    rng = np.random.default_rng(20261103)
    regions = [half_region(end, fmt) for end in CourtEnd for fmt in CourtFormat] + [
        service_box_region(end, side) for end in CourtEnd for side in ("deuce", "ad")
    ]
    normals = {"x_min": (-1.0, 0.0), "x_max": (1.0, 0.0), "y_min": (0.0, -1.0), "y_max": (0.0, 1.0)}
    seen = {verdict: 0 for verdict in LineVerdict}
    for _ in range(4000):
        region = regions[int(rng.integers(len(regions)))]
        edge = sorted(region.edge_lines)[int(rng.integers(len(region.edge_lines)))]
        x = rng.uniform(region.x_min, region.x_max)
        y = rng.uniform(region.y_min, region.y_max)
        offset = rng.normal(0.0, 0.4)
        x, y = {"x_min": (region.x_min + offset, y), "x_max": (region.x_max - offset, y),
                "y_min": (x, region.y_min + offset), "y_max": (x, region.y_max - offset)}[edge]
        sigmas = rng.uniform(0.005, 0.6, 2)
        angle = rng.uniform(0.0, math.pi)
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        cov = rotation @ np.diag(sigmas**2) @ rotation.T
        z = float(rng.choice([1.0, 1.96, 3.0]))
        bounce = BouncePoint("b", 1.0, 0.004, (float(x), float(y)), cov, CourtEnd.NEAR, 0.9, ())
        call = call_region(bounce, region, target=TARGET, z=z)
        seen[call.verdict] += 1
        margins = []
        for name in region.edge_lines:
            n = np.asarray(normals[name])
            inside = {"x_min": x - region.x_min, "x_max": region.x_max - x,
                      "y_min": y - region.y_min, "y_max": region.y_max - y}[name]
            margins.append((inside + BALL_CONTACT_PATCH_RADIUS_M, z * math.sqrt(float(n @ cov @ n))))
        in_band = [abs(m) < r for m, r in margins]
        if call.verdict is LineVerdict.IN:
            assert all(m >= r for m, r in margins)
            assert not any(in_band)
        elif call.verdict is LineVerdict.OUT:
            assert any(m <= -r for m, r in margins)
            assert call.signed_margin_m <= -call.uncertainty_radius_m
        else:
            assert not any(m <= -r for m, r in margins)
            assert any(in_band) or any(m < r for m, r in margins)
        if call.verdict is not LineVerdict.TOO_CLOSE_TO_CALL:
            assert abs(call.signed_margin_m) >= call.uncertainty_radius_m
    assert min(seen.values()) >= 300


# ---------------------------------------------------------------- G21


def test_calls_only_when_the_calibration_allows_them() -> None:
    six = calibration(RECOMMENDED_SIX)
    assert not six.calls_allowed and CALIBRATION.calls_allowed
    region = half_region(CourtEnd.FAR, CourtFormat.SINGLES)
    bounce = bounce_to_court(bounce_event(0.0, 6.0), six, STILL)
    assert call_bounce(bounce, region, six, target=TARGET) is None
    called = call_bounce(bounce, region, CALIBRATION, target=TARGET)
    assert called is not None and called.verdict is LineVerdict.IN
