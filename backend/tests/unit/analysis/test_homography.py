"""Build step 3: homography.py against known-truth synthetic cameras."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analysis.court_model import CourtKeypoint
from app.analysis.homography import (
    BALL_RADIUS_M,
    HOMOGRAPHY_VERSION,
    apply_homography,
    calibration_covariance,
    correct_ball_centre_parallax,
    decompose_camera_pose,
    fit_homography_dlt,
    image_scale_px_per_m,
    image_to_court_jacobian,
    invert_homography,
    normalization_transform,
    project_points_3d,
    reprojection_errors_px,
)
from tests.unit.analysis.synthetic_court import (
    RECOMMENDED_SIX,
    TruthCamera,
    keypoint_array,
    look_at_camera,
)

SEED = 20261102

CENTRED = look_at_camera()
YAWED = look_at_camera(position_m=(2.0, -18.5, 4.0), target_m=(-0.5, 0.0, 0.0), roll_deg=2.0)
CAMERAS = {"centred": CENTRED, "yawed": YAWED}


def _grid() -> np.ndarray:
    xs, ys = np.meshgrid(np.linspace(-5.5, 5.5, 12), np.linspace(-11.9, 11.9, 25))
    return np.column_stack([xs.ravel(), ys.ravel()])


def test_version() -> None:
    assert HOMOGRAPHY_VERSION == "homography_v1"


def test_normalization_transform_centres_and_scales() -> None:
    points = np.array([[100.0, 50.0], [900.0, 70.0], [400.0, 800.0], [1200.0, 600.0]])
    t = normalization_transform(points)
    normalised = apply_homography(t, points)
    assert np.allclose(normalised.mean(axis=0), 0.0, atol=1e-12)
    assert np.mean(np.linalg.norm(normalised, axis=1)) == pytest.approx(math.sqrt(2.0))


def test_normalization_rejects_coincident_points() -> None:
    with pytest.raises(ValueError):
        normalization_transform(np.ones((4, 2)))


@pytest.mark.parametrize("name", list(CAMERAS))
def test_exact_recovery_round_trip_within_1e6_m(name: str) -> None:
    camera = CAMERAS[name]
    court = keypoint_array(RECOMMENDED_SIX)
    image = camera.project_ground(court)
    h_ci, condition = fit_homography_dlt(court, image)
    assert condition > 1e-3
    assert h_ci[2, 2] == pytest.approx(1.0)
    assert np.allclose(h_ci, camera.h_court_to_image, rtol=1e-8, atol=1e-8)
    h_ic = invert_homography(h_ci)
    grid = _grid()
    round_trip = apply_homography(h_ic, camera.project_ground(grid))
    assert np.max(np.linalg.norm(round_trip - grid, axis=1)) <= 1e-6


def test_four_taps_fit_exactly_with_full_matrices() -> None:
    """With 4 taps A is 8x9; the null vector needs the full Vt (G1)."""
    court = keypoint_array(RECOMMENDED_SIX[:4])
    image = CENTRED.project_ground(court)
    h_ci, condition = fit_homography_dlt(court, image)
    assert condition > 1e-3
    assert np.max(reprojection_errors_px(h_ci, court, image)) < 1e-8


def test_condition_g1_good_taps_pass_and_near_collinear_fail() -> None:
    court = keypoint_array(RECOMMENDED_SIX)
    _, good = fit_homography_dlt(court, CENTRED.project_ground(court))
    assert good > 1e-3

    rng = np.random.default_rng(SEED)
    xs = np.linspace(-5.0, 5.0, 6)
    near_line = np.column_stack([xs, 0.5 * xs + rng.normal(0.0, 1e-5, 6)])
    _, bad = fit_homography_dlt(near_line, CENTRED.project_ground(near_line))
    assert bad < 1e-3


def test_dlt_residual_is_geometric_not_depth_weighted() -> None:
    """For image noise sigma, the geometric optimum has E[sum |e|^2] =
    sigma^2 (2N - 8). Plain algebraic DLT on a baseline view lands near 2x
    that, because it weights each tap by its projective depth."""
    court = keypoint_array(RECOMMENDED_SIX)
    clean = CENTRED.project_ground(court)
    rng = np.random.default_rng(SEED)
    totals = []
    for _ in range(1000):
        noisy = clean + rng.normal(0.0, 1.0, clean.shape)
        h_ci, _ = fit_homography_dlt(court, noisy)
        totals.append(float(np.sum(reprojection_errors_px(h_ci, court, noisy) ** 2)))
    assert float(np.mean(totals)) == pytest.approx(2 * 6 - 8, rel=0.08)


def test_fit_rejects_too_few_points() -> None:
    court = keypoint_array(RECOMMENDED_SIX[:3])
    with pytest.raises(ValueError):
        fit_homography_dlt(court, CENTRED.project_ground(court))


@pytest.mark.parametrize("name", list(CAMERAS))
def test_jacobian_matches_numeric_within_1e3(name: str) -> None:
    camera = CAMERAS[name]
    h_ic = invert_homography(camera.h_court_to_image)
    step = 1e-3
    for xy in ((0.0, -8.0), (3.9, 11.5), (-4.0, 6.0), (1.0, 0.5)):
        u, v = camera.project_ground(np.array([xy]))[0]
        analytic = image_to_court_jacobian(h_ic, (u, v))
        numeric = np.zeros((2, 2))
        for axis, delta in enumerate(((step, 0.0), (0.0, step))):
            plus = apply_homography(h_ic, np.array([[u + delta[0], v + delta[1]]]))[0]
            minus = apply_homography(h_ic, np.array([[u - delta[0], v - delta[1]]]))[0]
            numeric[:, axis] = (plus - minus) / (2.0 * step)
        assert np.max(np.abs(analytic - numeric)) <= 1e-3 * np.max(np.abs(numeric))


def test_reprojection_errors_are_tap_distances() -> None:
    court = keypoint_array(RECOMMENDED_SIX)
    image = CENTRED.project_ground(court)
    shifted = image.copy()
    shifted[2] += (3.0, 4.0)
    errors = reprojection_errors_px(CENTRED.h_court_to_image, court, shifted)
    assert errors[2] == pytest.approx(5.0)
    assert np.all(np.delete(errors, 2) < 1e-9)


@pytest.mark.parametrize("xy", [(0.0, 11.0), (-3.5, -9.0)])
def test_calibration_covariance_matches_monte_carlo_within_15_percent(
    xy: tuple[float, float],
) -> None:
    sigma_tap = 1.5
    court = keypoint_array(RECOMMENDED_SIX)
    image = CENTRED.project_ground(court)
    uv = CENTRED.project_ground(np.array([xy]))[0]
    analytic = calibration_covariance(court, image, (float(uv[0]), float(uv[1])), sigma_tap)

    rng = np.random.default_rng(SEED)
    samples = np.empty((4000, 2))
    for index in range(len(samples)):
        noisy = image + rng.normal(0.0, sigma_tap, image.shape)
        h_ic, _ = fit_homography_dlt(noisy, court)
        samples[index] = apply_homography(h_ic, uv[None, :])[0]
    empirical = np.cov(samples.T)
    relative = np.linalg.norm(empirical - analytic) / np.linalg.norm(analytic)
    assert relative <= 0.15
    assert analytic[0, 1] == pytest.approx(analytic[1, 0])


def test_calibration_covariance_scales_with_sigma_squared() -> None:
    court = keypoint_array(RECOMMENDED_SIX)
    image = CENTRED.project_ground(court)
    one = calibration_covariance(court, image, (960.0, 400.0), 1.0)
    two = calibration_covariance(court, image, (960.0, 400.0), 2.0)
    assert np.allclose(two, 4.0 * one)


def _assert_pose_close(camera: TruthCamera, h_ci: np.ndarray, tolerance: float) -> None:
    pose = decompose_camera_pose(h_ci, camera.frame_size)
    assert pose is not None
    assert pose.focal_px == pytest.approx(camera.focal_px, rel=tolerance)
    assert pose.height_m == pytest.approx(camera.position_m[2], rel=tolerance)
    distance = math.hypot(camera.position_m[0], camera.position_m[1])
    foot_error = math.hypot(
        pose.foot_xy_m[0] - camera.position_m[0], pose.foot_xy_m[1] - camera.position_m[1]
    )
    assert foot_error <= tolerance * distance
    assert pose.position_m == (pose.foot_xy_m[0], pose.foot_xy_m[1], pose.height_m)


@pytest.mark.parametrize("name", list(CAMERAS))
def test_pose_recovered_within_2_percent_exact(name: str) -> None:
    """The centred view is the degenerate case for orthogonality alone."""
    camera = CAMERAS[name]
    _assert_pose_close(camera, camera.h_court_to_image, 0.02)


def test_centred_view_really_is_orthogonality_degenerate() -> None:
    """h31 = 0 for an unrolled camera on the centre line: court x has no
    vanishing point, so the orthogonality constraint alone says nothing."""
    h = CENTRED.h_court_to_image
    assert abs(h[2, 0]) < 1e-12


@pytest.mark.parametrize("name", list(CAMERAS))
def test_pose_recovered_within_2_percent_from_noisy_taps(name: str) -> None:
    camera = CAMERAS[name]
    court = keypoint_array(list(CourtKeypoint))
    rng = np.random.default_rng(SEED)
    image = camera.project_ground(court) + rng.normal(0.0, 0.5, (len(court), 2))
    h_ci, _ = fit_homography_dlt(court, image)
    _assert_pose_close(camera, h_ci, 0.02)


def test_pose_rejects_a_homography_no_camera_can_make() -> None:
    """A pure affine stretch (h31 = h32 = 0) with unequal column norms has no
    positive f^2."""
    h = np.array([[50.0, 0.0, 960.0], [0.0, 10.0, 540.0], [0.0, 0.0, 1.0]])
    assert decompose_camera_pose(h, (1920, 1080)) is None


@pytest.mark.parametrize("name", list(CAMERAS))
def test_project_points_3d_matches_truth_g6(name: str) -> None:
    camera = CAMERAS[name]
    pose = decompose_camera_pose(camera.h_court_to_image, camera.frame_size)
    assert pose is not None
    assert pose.projection_matrix.shape == (3, 4)
    points = np.array([[0.0, 0.0, 0.914], [-6.4, 0.0, 1.07], [2.0, 8.0, 2.5], [1.0, -5.0, 0.0]])
    assert np.allclose(project_points_3d(pose, points), camera.project(points), atol=1e-6)


@pytest.mark.parametrize("name", list(CAMERAS))
def test_parallax_correction_is_exact(name: str) -> None:
    camera = CAMERAS[name]
    pose = decompose_camera_pose(camera.h_court_to_image, camera.frame_size)
    assert pose is not None
    h_ic = invert_homography(camera.h_court_to_image)
    for x, y in ((0.0, 11.0), (-3.0, -10.0), (4.0, 2.0)):
        centre_px = camera.project(np.array([[x, y, BALL_RADIUS_M]]))
        naive = apply_homography(h_ic, centre_px)[0]
        assert math.hypot(naive[0] - x, naive[1] - y) > 1e-3  # the bias is real
        corrected = correct_ball_centre_parallax((naive[0], naive[1]), pose)
        assert corrected == pytest.approx((x, y), abs=1e-9)


def test_image_scale_is_focal_over_range() -> None:
    camera = CENTRED
    pose = decompose_camera_pose(camera.h_court_to_image, camera.frame_size)
    assert pose is not None
    h_ic = invert_homography(camera.h_court_to_image)
    principal = (camera.frame_size[0] / 2.0, camera.frame_size[1] / 2.0)
    scale = image_scale_px_per_m(pose, h_ic, principal)
    ground = apply_homography(h_ic, np.array([principal]))[0]
    # A short segment facing the camera at that point, along camera-right.
    right = camera.rotation[0]
    centre = np.array([ground[0], ground[1], 0.0])
    ends = np.vstack([centre - 0.01 * right, centre + 0.01 * right])
    projected = camera.project(ends)
    measured = float(np.linalg.norm(projected[1] - projected[0])) / 0.02
    assert scale == pytest.approx(measured, rel=1e-3)
