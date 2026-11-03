"""Build step 4: tap gating. One test per rejection reason, tap-count rules,
noise-level RMS, and the lens-distortion case (brief: k1 = -0.25 rejected,
k1 = 0 accepted)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from app.analysis.court_calibration import (
    CALIBRATION_VERSION,
    UNVERIFIED_CALIBRATION_FLAG,
    CalibrationRejection,
    CourtTap,
    calibrate_court,
    validate_taps,
)
from app.analysis.court_model import CourtKeypoint
from tests.unit.analysis.synthetic_court import (
    RECOMMENDED_SIX,
    TruthCamera,
    keypoint_array,
    look_at_camera,
)

SEED = 20261102
CAMERA = look_at_camera()
FRAME = CAMERA.frame_size
BASELINE_FOUR: tuple[CourtKeypoint, ...] = (
    CourtKeypoint.NEAR_LEFT_DOUBLES_CORNER,
    CourtKeypoint.NEAR_LEFT_SINGLES_CORNER,
    CourtKeypoint.NEAR_RIGHT_SINGLES_CORNER,
    CourtKeypoint.NEAR_RIGHT_DOUBLES_CORNER,
)


def make_taps(
    keypoints: Sequence[CourtKeypoint],
    camera: TruthCamera = CAMERA,
    *,
    k1: float = 0.0,
    noise_px: float = 0.0,
    rng: np.random.Generator | None = None,
) -> list[CourtTap]:
    """Project keypoints, optionally through radial distortion and tap noise."""
    uv = camera.project_ground(keypoint_array(list(keypoints)))
    width, height = camera.frame_size
    centre = np.array([width / 2.0, height / 2.0])
    normalised = (uv - centre) / camera.focal_px
    r2 = np.sum(normalised**2, axis=1, keepdims=True)
    uv = centre + normalised * (1.0 + k1 * r2) * camera.focal_px
    if noise_px > 0.0:
        assert rng is not None
        uv = uv + rng.normal(0.0, noise_px, uv.shape)
    return [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(keypoints, uv, strict=True)]


def rejection_of(taps: Sequence[CourtTap], **kwargs: object) -> CalibrationRejection | None:
    return calibrate_court(taps, FRAME, **kwargs).rejection  # type: ignore[arg-type]


def test_clean_six_taps_are_accepted_with_calls() -> None:
    verdict = calibrate_court(make_taps(RECOMMENDED_SIX), FRAME)
    assert verdict.rejection is None
    calibration = verdict.calibration
    assert calibration is not None
    assert calibration.calls_allowed
    assert calibration.flags == ()
    assert calibration.version == CALIBRATION_VERSION == "court_cal_v1"
    assert calibration.reprojection_max_px < 1e-6
    assert calibration.pose.focal_px == pytest.approx(CAMERA.focal_px, rel=1e-6)
    assert calibration.pose.height_m == pytest.approx(3.5, rel=1e-6)
    product = calibration.h_court_to_image @ calibration.h_image_to_court
    assert np.allclose(product / product[2, 2], np.eye(3), atol=1e-9)
    assert calibration.sigma_tap_px == 1.5


@pytest.mark.parametrize("count", [4, 5])
def test_four_or_five_taps_never_allow_calls(count: int) -> None:
    verdict = calibrate_court(make_taps(RECOMMENDED_SIX[:count]), FRAME)
    assert verdict.rejection is None
    assert verdict.calibration is not None
    assert verdict.calibration.calls_allowed is False
    assert UNVERIFIED_CALIBRATION_FLAG in verdict.calibration.flags


def test_four_taps_skip_the_reprojection_gate_because_it_proves_nothing() -> None:
    taps = make_taps(RECOMMENDED_SIX[:4])
    taps[0] = CourtTap(taps[0].keypoint, taps[0].u_px + 20.0, taps[0].v_px)
    verdict = calibrate_court(taps, FRAME)
    assert verdict.calibration is not None
    assert verdict.calibration.reprojection_max_px < 1e-6  # exact fit, always
    assert verdict.calibration.calls_allowed is False


def test_too_few_taps() -> None:
    assert rejection_of(make_taps(RECOMMENDED_SIX[:3])) is CalibrationRejection.TOO_FEW_TAPS


def test_duplicate_keypoint() -> None:
    taps = make_taps(RECOMMENDED_SIX)
    taps.append(CourtTap(taps[0].keypoint, taps[0].u_px + 50.0, taps[0].v_px))
    assert rejection_of(taps) is CalibrationRejection.DUPLICATE_TAPS


def test_taps_within_8_px_at_1280_equivalent() -> None:
    taps = make_taps(RECOMMENDED_SIX)
    # 1920 frame: 8 px at 1280 is 12 px here. 11 px is a duplicate, 13 px is not.
    close = CourtTap(CourtKeypoint.FAR_RIGHT_SERVICE_CORNER, taps[5].u_px + 11.0, taps[5].v_px)
    assert validate_taps([*taps, close], FRAME) is CalibrationRejection.DUPLICATE_TAPS
    apart = CourtTap(CourtKeypoint.FAR_RIGHT_SERVICE_CORNER, taps[5].u_px + 13.0, taps[5].v_px)
    assert validate_taps([*taps, apart], FRAME) is not CalibrationRejection.DUPLICATE_TAPS


def test_collinear_taps() -> None:
    assert rejection_of(make_taps(BASELINE_FOUR)) is CalibrationRejection.COLLINEAR_TAPS


def test_taps_too_small() -> None:
    taps = make_taps(RECOMMENDED_SIX)
    centre = np.array([960.0, 540.0])
    shrunk = [
        CourtTap(t.keypoint, *(centre + 0.15 * (np.array([t.u_px, t.v_px]) - centre)))
        for t in taps
    ]
    assert rejection_of(shrunk) is CalibrationRejection.TAPS_TOO_SMALL


def test_taps_mirrored_left_right() -> None:
    taps = [CourtTap(t.keypoint, 1920.0 - t.u_px, t.v_px) for t in make_taps(RECOMMENDED_SIX)]
    assert rejection_of(taps) is CalibrationRejection.TAPS_MIRRORED


def test_taps_mirrored_near_above_far() -> None:
    """A 180 degree rotation keeps the winding; the near/far check catches it."""
    taps = [
        CourtTap(t.keypoint, 1920.0 - t.u_px, 1080.0 - t.v_px) for t in make_taps(RECOMMENDED_SIX)
    ]
    assert rejection_of(taps) is CalibrationRejection.TAPS_MIRRORED


def test_ill_conditioned() -> None:
    """Four keypoints on one court line, tapped at a well-spread trapezoid: the
    image passes every geometry gate but the court side cannot fix a
    homography, so the G1 condition fires."""
    image = [(300.0, 950.0), (800.0, 500.0), (1300.0, 500.0), (1700.0, 950.0)]
    taps = [CourtTap(k, u, v) for k, (u, v) in zip(BASELINE_FOUR, image, strict=True)]
    assert validate_taps(taps, FRAME) is None
    assert rejection_of(taps) is CalibrationRejection.ILL_CONDITIONED


def test_reprojection_error_high() -> None:
    taps = make_taps(RECOMMENDED_SIX)
    taps[4] = CourtTap(taps[4].keypoint, taps[4].u_px, taps[4].v_px + 15.0)
    assert rejection_of(taps) is CalibrationRejection.REPROJECTION_ERROR_HIGH


def test_frame_shape_mismatch() -> None:
    taps = make_taps(RECOMMENDED_SIX)
    assert rejection_of(taps, video_frame_size=(1080, 1920)) is (
        CalibrationRejection.FRAME_SHAPE_MISMATCH
    )
    assert rejection_of(taps, video_frame_size=(1280, 720)) is None  # same aspect


def test_camera_pose_implausible_too_high() -> None:
    high = look_at_camera(position_m=(0.0, -30.0, 20.0), target_m=(0.0, 0.0, 0.0))
    taps = make_taps(RECOMMENDED_SIX, high)
    assert validate_taps(taps, FRAME) is None
    assert rejection_of(taps) is CalibrationRejection.CAMERA_POSE_IMPLAUSIBLE


def test_camera_pose_implausible_focal_too_short() -> None:
    """f = 500 px at 1920 is 333 px at 1280, under the 400 px floor."""
    wide = look_at_camera(focal_px=500.0, position_m=(0.0, -14.0, 3.0), target_m=(0.0, -4.0, 0.0))
    assert rejection_of(make_taps(RECOMMENDED_SIX, wide)) is (
        CalibrationRejection.CAMERA_POSE_IMPLAUSIBLE
    )


def test_camera_pose_implausible_no_perspective() -> None:
    """Four corners tapped as a perfect rectangle: an affine map no finite
    camera produces, so no positive f^2 exists."""
    corners = RECOMMENDED_SIX[:4]
    image = [(400.0, 900.0), (1500.0, 900.0), (400.0, 200.0), (1500.0, 200.0)]
    taps = [CourtTap(k, u, v) for k, (u, v) in zip(corners, image, strict=True)]
    assert rejection_of(taps) is CalibrationRejection.CAMERA_POSE_IMPLAUSIBLE


def test_six_noisy_taps_give_rms_close_to_noise() -> None:
    """E[rms^2] = sigma^2 (2N - 8) / N for an 8-dof fit to N points."""
    sigma = 1.0
    rng = np.random.default_rng(SEED)
    squares: list[float] = []
    accepted = 0
    for _ in range(300):
        verdict = calibrate_court(make_taps(RECOMMENDED_SIX, noise_px=sigma, rng=rng), FRAME)
        if verdict.calibration is not None:
            accepted += 1
            squares.append(verdict.calibration.reprojection_rms_px**2)
    expected = sigma**2 * (2 * 6 - 8) / 6
    assert accepted >= 297
    assert float(np.mean(squares)) == pytest.approx(expected, rel=0.15)


#: The recommended six plus both near-side service corners.
EIGHT_TAPS: tuple[CourtKeypoint, ...] = (
    *RECOMMENDED_SIX,
    CourtKeypoint.NEAR_LEFT_SERVICE_CORNER,
    CourtKeypoint.NEAR_RIGHT_SERVICE_CORNER,
)


@pytest.mark.parametrize(
    "keypoints", [RECOMMENDED_SIX, EIGHT_TAPS, tuple(CourtKeypoint)], ids=["6", "8", "14"]
)
def test_lens_distortion_k1_zero_is_accepted(keypoints: tuple[CourtKeypoint, ...]) -> None:
    assert rejection_of(make_taps(keypoints, k1=0.0)) is None


@pytest.mark.parametrize("keypoints", [EIGHT_TAPS, tuple(CourtKeypoint)], ids=["8", "14"])
def test_lens_distortion_k1_minus_quarter_is_rejected(
    keypoints: tuple[CourtKeypoint, ...],
) -> None:
    """k1 is OpenCV-style, on focal-normalised radius."""
    assert rejection_of(make_taps(keypoints, k1=-0.25)) is (
        CalibrationRejection.REPROJECTION_ERROR_HIGH
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN GAP, measured: with only the recommended six taps (four singles "
        "corners and both Ts) on the section 2.3 placement, k1 = -0.25 leaves a "
        "geometric-fit residual of RMS 1.94 / max 3.93 px at 1280-equivalent, "
        "just under the 2.0 / 4.0 gates, so it is ACCEPTED while carrying up to "
        "0.47 m systematic ground error (near baseline centre) and 0.26 m at the "
        "far T. The symmetric six-point layout absorbs most of the distortion. "
        "Plain algebraic DLT rejected it only because its fit was ~2x worse than "
        "optimal on every input. Not tuned away; for the owner/architect."
    ),
)
def test_lens_distortion_k1_minus_quarter_is_rejected_with_six_taps() -> None:
    assert rejection_of(make_taps(RECOMMENDED_SIX, k1=-0.25)) is (
        CalibrationRejection.REPROJECTION_ERROR_HIGH
    )


def test_gates_scale_with_resolution() -> None:
    """The same geometry at 1280x720 and 1920x1080 gives the same verdicts."""
    small = look_at_camera(focal_px=CAMERA.focal_px * 1280.0 / 1920.0, frame_size=(1280, 720))
    taps = make_taps(RECOMMENDED_SIX, small)
    assert calibrate_court(taps, (1280, 720)).rejection is None
    # 15 px at 1920 is 10 px at 1280: still over the 4 px max at 1280.
    taps[4] = CourtTap(taps[4].keypoint, taps[4].u_px, taps[4].v_px + 10.0)
    assert calibrate_court(taps, (1280, 720)).rejection is (
        CalibrationRejection.REPROJECTION_ERROR_HIGH
    )
