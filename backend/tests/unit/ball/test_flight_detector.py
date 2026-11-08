"""Build step 7: ``app/ball/flight_detector.py`` (design 4.3, brief G2 and G4).

Done when: it finds 2-3 px synthetic balls that the existing ``find_candidates``
misses (both asserted), static lines give no candidates, and tiny 2x2 and 2x1
blobs get features from connected-component moments.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from app.analysis import flight_track
from app.analysis.court_calibration import CourtCalibration, CourtTap, calibrate_court
from app.analysis.court_model import CourtKeypoint
from app.ball import flight_detector
from app.ball.detector import contour_features, find_candidates
from app.ball.flight_detector import (
    COLOUR_GATE_DIAMETER_PX,
    FLIGHT_CANDIDATE_DTYPE,
    MOTION_DIFF_FLOOR,
    court_roi_mask,
    double_difference_mask,
    expected_diameter_fn,
    find_flight_candidates,
)
from tests.unit.analysis.synthetic_court import (
    RECOMMENDED_SIX,
    TruthCamera,
    keypoint_array,
    look_at_camera,
)

U8 = npt.NDArray[np.uint8]
SEED = 20261102
COURT_BGR = (150, 95, 45)
BALL_BGR = (60, 225, 215)
GREY_BGR = (120, 120, 120)
SIZE = (480, 270)  # (width, height)
EIGHT_TAPS: tuple[CourtKeypoint, ...] = (
    *RECOMMENDED_SIX,
    CourtKeypoint.NEAR_LEFT_SERVICE_CORNER,
    CourtKeypoint.NEAR_RIGHT_SERVICE_CORNER,
)


def court_frame(seed: int, noise: float = 2.0, size: tuple[int, int] = SIZE) -> U8:
    rng = np.random.default_rng(seed)
    width, height = size
    frame = np.empty((height, width, 3), dtype=np.float64)
    frame[:] = COURT_BGR
    frame += rng.normal(0.0, noise, frame.shape)
    return np.clip(np.rint(frame), 0, 255).astype(np.uint8)


def draw_disc(frame: U8, centre: tuple[float, float], diameter: float, bgr: tuple[int, int, int]) -> U8:
    """Anti-aliased disc by 8x supersampled coverage. OpenCV convention: pixel
    (i, j) is centred on integer coordinates."""
    out = frame.astype(np.float64)
    ss = 8
    radius = diameter / 2.0
    x0, y0 = int(math.floor(centre[0] - radius - 1)), int(math.floor(centre[1] - radius - 1))
    x1, y1 = int(math.ceil(centre[0] + radius + 1)), int(math.ceil(centre[1] + radius + 1))
    ys, xs = np.mgrid[y0 * ss : y1 * ss, x0 * ss : x1 * ss]
    px = (xs + 0.5) / ss - 0.5
    py = (ys + 0.5) / ss - 0.5
    inside = (px - centre[0]) ** 2 + (py - centre[1]) ** 2 <= radius**2
    coverage = inside.reshape(y1 - y0, ss, x1 - x0, ss).mean(axis=(1, 3))[:, :, None]
    patch = out[y0:y1, x0:x1]
    out[y0:y1, x0:x1] = patch * (1.0 - coverage) + np.array(bgr) * coverage
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def grey(frame: U8) -> U8:
    return np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)


def detect_middle(frames: list[U8], **kwargs: object) -> npt.NDArray[np.void]:
    prev, cur, nxt = frames
    return find_flight_candidates(grey(prev), grey(cur), grey(nxt), cur, 7, 0.125, **kwargs)  # type: ignore[arg-type]


def moving_ball_frames(
    diameter: float, *, bgr: tuple[int, int, int] = BALL_BGR, step: float = 12.0
) -> tuple[list[U8], tuple[float, float]]:
    centres = [(200.0 + k * step + 0.3, 140.0 - k * 2.0 + 0.6) for k in range(3)]
    frames = [draw_disc(court_frame(SEED + k), c, diameter, bgr) for k, c in enumerate(centres)]
    return frames, centres[1]


def nearest(records: npt.NDArray[np.void], xy: tuple[float, float]) -> float:
    if len(records) == 0:
        return math.inf
    return float(np.min(np.hypot(records["x"] - xy[0], records["y"] - xy[1])))


def plain_frames(blocks: list[tuple[int, int, int, int]]) -> list[U8]:
    """Flat grey prev/next, and a cur frame with bright (row0, row1, col0, col1) blocks."""
    base = np.full((60, 80, 3), 100, dtype=np.uint8)
    cur = base.copy()
    for r0, r1, c0, c1 in blocks:
        cur[r0:r1, c0:c1] = 200
    return [base.copy(), cur, base.copy()]


# ---------------------------------------------------------------- dtype (G4)


def test_dtype_is_owned_by_flight_track_and_re_exported() -> None:
    assert flight_detector.FLIGHT_CANDIDATE_DTYPE is flight_track.FLIGHT_CANDIDATE_DTYPE
    assert FLIGHT_CANDIDATE_DTYPE.names == (
        "frame_index", "time_s", "x", "y", "area", "minor", "major",
        "orientation_deg", "colour_score", "contrast",
    )
    assert FLIGHT_CANDIDATE_DTYPE.itemsize == 56


# ------------------------------------------------------- the done criterion


@pytest.mark.parametrize("diameter", [2.0, 2.5, 3.0])
def test_finds_2_to_3_px_ball_that_existing_find_candidates_misses(diameter: float) -> None:
    frames, truth = moving_ball_frames(diameter)
    records = detect_middle(frames)
    assert nearest(records, truth) <= 0.75

    background = np.median(np.stack([grey(court_frame(SEED + 100 + k)) for k in range(5)]), axis=0)
    existing = find_candidates(frames[1], background.astype(np.uint8), 1)
    assert all(math.hypot(c.x - truth[0], c.y - truth[1]) > 5.0 for c in existing)
    assert existing == []


# ------------------------------------------------- tiny-blob path (brief G2)


def test_two_by_two_blob_features_from_component_moments() -> None:
    records = detect_middle(plain_frames([(20, 22, 30, 32)]))
    assert len(records) == 1
    record = records[0]
    assert record["area"] == 4.0
    assert (record["x"], record["y"]) == (30.5, 20.5)
    assert record["major"] == pytest.approx(2.0)
    assert record["minor"] == pytest.approx(2.0)
    assert record["contrast"] == 100.0
    assert record["frame_index"] == 7 and record["time_s"] == 0.125


def test_two_by_one_blob_features_and_orientation() -> None:
    horizontal = detect_middle(plain_frames([(20, 21, 30, 32)]))
    vertical = detect_middle(plain_frames([(20, 22, 50, 51)]))
    for records, orientation, centre in (
        (horizontal, 0.0, (30.5, 20.0)),
        (vertical, 90.0, (50.0, 20.5)),
    ):
        assert len(records) == 1
        record = records[0]
        assert record["area"] == 2.0
        assert (record["x"], record["y"]) == centre
        assert record["major"] == pytest.approx(2.0)
        assert record["minor"] == pytest.approx(1.0)
        assert record["orientation_deg"] == pytest.approx(orientation)


def test_existing_contour_features_drops_the_two_by_one_blob() -> None:
    """The reason for G2: a 2x1 polygon has zero area."""
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[4, 4:6] = 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    assert contour_features(contours[0]) is None


def test_single_pixel_is_under_the_2_px_area_floor() -> None:
    assert len(detect_middle(plain_frames([(20, 21, 30, 31)]))) == 0


def test_tiny_path_switch_follows_expected_diameter() -> None:
    """G2: moments below 6 px expected, contours above it (the 2x1 blob then
    has a degenerate contour and is dropped)."""
    frames = plain_frames([(20, 21, 30, 32)])
    assert len(detect_middle(frames, expected_diameter_px=lambda x, y: 3.0)) == 1
    assert len(detect_middle(frames, expected_diameter_px=lambda x, y: 8.0)) == 0
    assert len(detect_middle(frames, expected_diameter_px=None)) == 1


def test_diagonal_moments_give_45_degrees() -> None:
    frames = plain_frames([(10, 11, 10, 11), (11, 12, 11, 12), (12, 13, 12, 13)])
    records = detect_middle(frames)
    assert len(records) == 1
    assert records[0]["orientation_deg"] == pytest.approx(45.0)
    assert records[0]["minor"] < records[0]["major"]


# ------------------------------------------------------------- motion mask


def test_static_lines_with_noise_and_drift_give_no_candidates() -> None:
    """Lines add no motion and no candidate over the same noise and drift
    without lines. Sigma-3 sensor noise alone leaves isolated pixels (under the
    2 px floor) and the odd adjacent pair, which the linker must reject."""
    with_lines: list[U8] = []
    without: list[U8] = []
    for k, gain in enumerate((1.0, 1.005, 1.01)):
        frame = court_frame(SEED + 10 + k, noise=3.0).astype(np.float64)
        without.append(np.clip(np.rint(frame * gain), 0, 255).astype(np.uint8))
        frame[100:103, :, :] = 235.0  # baseline
        frame[:, 240:242, :] = 235.0  # centre line
        cv2.line(frame, (20, 260), (300, 20), (235.0, 235.0, 235.0), 2, cv2.LINE_AA)
        with_lines.append(np.clip(np.rint(frame * gain), 0, 255).astype(np.uint8))
    lines_mask = double_difference_mask(*[grey(f) for f in with_lines]) > 0
    noise_mask = double_difference_mask(*[grey(f) for f in without]) > 0
    assert not np.any(lines_mask & ~noise_mask)
    from_lines = detect_middle(with_lines)
    from_noise = detect_middle(without)
    assert from_lines.tobytes() == from_noise.tobytes()
    assert len(from_lines) <= 2  # noise pairs only
    assert np.all(from_lines["area"] <= 3.0)


def test_threshold_rises_with_noise_mad() -> None:
    """At sigma 8 a fixed 8-level threshold marks ~10 % of the frame; 4 x MAD
    marks under 0.1 %."""
    frames = [grey(court_frame(SEED + 20 + k, noise=8.0)) for k in range(3)]
    mask = double_difference_mask(*frames)
    assert np.count_nonzero(mask) / mask.size < 1e-3
    fixed = (
        (np.abs(frames[1].astype(int) - frames[0].astype(int)) > MOTION_DIFF_FLOOR)
        & (np.abs(frames[2].astype(int) - frames[1].astype(int)) > MOTION_DIFF_FLOOR)
    )
    assert np.count_nonzero(fixed) / fixed.size > 0.05


@pytest.mark.parametrize(
    ("values", "expected"),
    [([-3, -1, 0, 1, 3], MOTION_DIFF_FLOOR), ([-20, -5, 0, 5, 20], 20), ([7, 7, 7], MOTION_DIFF_FLOOR)],
)
def test_mad_threshold_exact(values: list[int], expected: int) -> None:
    signed = np.array(values * 11, dtype=np.int16)
    assert flight_detector._mad_threshold(signed) == expected


def test_trailing_ghost_is_removed() -> None:
    """Only the current position survives, not where the ball was or will be."""
    frames, truth = moving_ball_frames(6.0)
    records = detect_middle(frames)
    assert len(records) == 1
    assert nearest(records, truth) <= 0.5


def test_closing_does_not_shift_the_blob() -> None:
    frames = plain_frames([(20, 22, 30, 32)])
    mask = double_difference_mask(*[grey(f) for f in frames])
    assert np.argwhere(mask > 0).tolist() == [[20, 30], [20, 31], [21, 30], [21, 31]]


# ------------------------------------------------- size and colour gates


def test_size_gate_rejects_blobs_far_larger_than_expected() -> None:
    frames, truth = moving_ball_frames(12.0, step=20.0)
    assert len(detect_middle(frames, expected_diameter_px=lambda x, y: 2.0)) == 0
    kept = detect_middle(frames, expected_diameter_px=lambda x, y: 10.0)
    assert nearest(kept, truth) <= 0.5


def test_colour_is_a_score_below_4_px_and_a_gate_above() -> None:
    small, _ = moving_ball_frames(3.0, bgr=GREY_BGR, step=16.0)
    below = detect_middle(small, expected_diameter_px=lambda x, y: COLOUR_GATE_DIAMETER_PX - 1.0)
    assert len(below) == 1 and below[0]["colour_score"] == 0.0
    frames, truth = moving_ball_frames(8.0, bgr=GREY_BGR, step=16.0)
    assert len(detect_middle(frames, expected_diameter_px=lambda x, y: 8.0)) == 0
    # The blob's OWN size triggers the gate too (T2: far-court clutter 4+ px
    # across where the ground estimate said 2 px).
    assert len(detect_middle(frames, expected_diameter_px=lambda x, y: COLOUR_GATE_DIAMETER_PX - 1.0)) == 0
    yellow, _ = moving_ball_frames(8.0, step=16.0)
    above = detect_middle(yellow, expected_diameter_px=lambda x, y: 8.0)
    assert len(above) == 1 and above[0]["colour_score"] > 0.5


def test_rejects_shapes_the_existing_tiers_reject() -> None:
    """Above 6 px the existing round/streak logic applies: a ragged blob fails."""
    blocks = [(10, 40, 10, 12), (10, 12, 10, 40)]  # an L, solidity far under 0.8
    assert len(detect_middle(plain_frames(blocks), expected_diameter_px=lambda x, y: 8.0)) == 0


def test_output_is_deterministic() -> None:
    frames, _ = moving_ball_frames(2.5)
    first = detect_middle(frames)
    second = detect_middle([f.copy() for f in frames])
    assert first.tobytes() == second.tobytes()


# ------------------------------------------- ROI and expected size (calibrated)


def calibration_for(camera: TruthCamera) -> CourtCalibration:
    uv = camera.project_ground(keypoint_array(list(EIGHT_TAPS)))
    taps = [CourtTap(k, float(u), float(v)) for k, (u, v) in zip(EIGHT_TAPS, uv, strict=True)]
    verdict = calibrate_court(taps, camera.frame_size)
    assert verdict.calibration is not None
    return verdict.calibration


def mask_value(camera: TruthCamera, mask: U8, xyz: tuple[float, float, float]) -> int:
    u, v = camera.project(np.array([xyz]))[0]
    assert 0 <= u < camera.frame_size[0] and 0 <= v < camera.frame_size[1]
    return int(mask[int(v), int(u)])


def test_court_roi_mask_is_the_image_of_the_court_runoff_air_volume() -> None:
    """Design 4.3 volume: court + 4 m run-off, up to 7 m. From the recommended
    placement (6 m back, 3.5 m high) the camera is 2 m from that volume, so
    EVERY viewing ray crosses it and the mask is the whole frame. The design's
    "removes most adjacent-court activity" does not hold there; recorded, not
    tuned away. From farther back, points whose rays miss the volume drop out."""
    camera = look_at_camera()
    mask = court_roi_mask(calibration_for(camera), camera.frame_size)
    assert mask.shape == (1080, 1920) and mask.dtype == np.uint8
    assert np.all(mask == 255)

    far = look_at_camera(position_m=(0.0, -30.0, 6.0), target_m=(0.0, 0.0, 0.0))
    far_mask = court_roi_mask(calibration_for(far), far.frame_size)
    assert mask_value(far, far_mask, (0.0, 0.0, 0.0)) == 255
    assert mask_value(far, far_mask, (5.485 + 3.8, 6.0, 0.0)) == 255  # run-off
    assert mask_value(far, far_mask, (0.0, 11.885 + 3.8, 0.0)) == 255
    assert mask_value(far, far_mask, (0.0, 0.0, 6.5)) == 255  # a lob
    assert mask_value(far, far_mask, (0.0, 30.0, 14.0)) == 0  # sky over the far fence
    # Rays through the frame corners and the bottom-centre miss the volume.
    assert far_mask[0, 0] == far_mask[1079, 0] == far_mask[1079, 1919] == far_mask[1079, 960] == 0
    assert 0.5 < float(np.mean(far_mask == 255)) < 0.8


def test_court_roi_mask_rescales_to_the_requested_frame() -> None:
    camera = look_at_camera()
    calibration = calibration_for(camera)
    full = court_roi_mask(calibration, (1920, 1080))
    half = court_roi_mask(calibration, (960, 540))
    downsampled = cv2.resize(full, (960, 540), interpolation=cv2.INTER_NEAREST)
    assert np.mean(half != downsampled) < 0.01


def test_court_roi_mask_is_whole_frame_when_the_box_is_behind_the_camera() -> None:
    """Camera on the near baseline: the near run-off is behind it."""
    camera = look_at_camera(position_m=(0.0, -12.5, 3.5), target_m=(0.0, 0.0, 0.0), focal_px=1200.0)
    mask = court_roi_mask(calibration_for(camera), camera.frame_size)
    assert np.all(mask == 255)


def test_expected_diameter_matches_the_truth_camera() -> None:
    camera = look_at_camera()
    diameter = expected_diameter_fn(calibration_for(camera))
    for xy in ((0.0, 0.0), (3.0, 11.0), (-4.0, -10.0)):
        u, v = camera.project_ground(np.array([xy]))[0]
        distance = math.dist((*xy, 0.0), camera.position_m)
        assert diameter(u, v) == pytest.approx(0.067 * camera.focal_px / distance, rel=1e-6)


def test_roi_mask_suppresses_motion_outside_it() -> None:
    frames, truth = moving_ball_frames(3.0)
    roi = np.zeros(frames[0].shape[:2], dtype=np.uint8)
    assert len(detect_middle(frames, roi_mask=roi)) == 0
    roi[:] = 255
    assert nearest(detect_middle(frames, roi_mask=roi), truth) <= 0.75


# ---------------------------------------------------- T1 pixels (court_synth)


@pytest.mark.court_pixels
def test_t1_far_court_ball_found_where_existing_detector_finds_nothing() -> None:
    """T1 (clean render, 1280x720): far-half frames whose ball renders 2-3 px
    across. Ours finds it within 1.5 px in most frames; the existing
    ``find_candidates`` (given the exact static background) never does."""
    from tests.unit.court_synth.camera import default_camera
    from tests.unit.court_synth.render import CourtRenderer, RenderOptions
    from tests.unit.court_synth.scene import BallRole, generate_rally

    scene = generate_rally(SEED)
    camera = default_camera((1280, 720))
    renderer = CourtRenderer(scene, camera, RenderOptions.clean(fps=60.0), SEED)
    ball = next(b for b in scene.balls if b.role is BallRole.MAIN)
    background = grey(renderer.static)

    chosen: list[int] = []
    for index in range(1, renderer.frame_count - 1):
        time_s = float(renderer.frame_times[index])
        if not ball.trajectory.start_s <= time_s <= ball.trajectory.end_s:
            continue
        position = ball.trajectory.position_at(time_s)[0]
        diameter = 2.0 * float(camera.radius_px(position, ball.radius_m)[0])
        uv = renderer.ball_uv(index, ball)
        if position[1] > 6.0 and 2.0 <= diameter <= 3.0 and np.all(np.isfinite(uv)):
            chosen.append(index)
    chosen = chosen[:: max(1, len(chosen) // 20)]
    assert len(chosen) >= 10

    ours = 0
    for index in chosen:
        frames = [renderer.render_frame(i) for i in (index - 1, index, index + 1)]
        uv = renderer.ball_uv(index, ball)
        truth = (float(uv[0]), float(uv[1]))
        records = find_flight_candidates(
            grey(frames[0]), grey(frames[1]), grey(frames[2]), frames[1], index, 0.0
        )
        ours += int(nearest(records, truth) <= 1.5)
        existing = find_candidates(frames[1], background, index)
        assert all(math.hypot(c.x - truth[0], c.y - truth[1]) > 3.0 for c in existing)
    assert ours / len(chosen) >= 0.8



# ------------------------------------------------ T2 fixes (2026-11-08)


def test_max_diameter_exceeds_the_ground_estimate_for_an_airborne_ball() -> None:
    """Regression (T2 seed 20270000): a ball 2.8 m up over the net measured
    6.4 px against 1.2 px expected on the ground, and the size gate dropped it."""
    from app.ball.flight_detector import max_diameter_fn

    camera = look_at_camera()
    calibration = calibration_for(camera)
    ground = expected_diameter_fn(calibration)
    largest = max_diameter_fn(calibration)
    ball = np.array([[0.0, 0.0, 2.8]])
    u, v = camera.project(ball)[0]
    true_diameter = 0.067 * camera.focal_px / math.dist((0.0, 0.0, 2.8), camera.position_m)
    assert ground(u, v) < true_diameter <= largest(u, v)


def test_a_faint_blob_below_the_contrast_floor_is_dropped() -> None:
    from app.ball.flight_detector import MIN_CONTRAST_FACTOR, MOTION_DIFF_FLOOR

    faint, _ = moving_ball_frames(3.0, bgr=(120, 120, 120), step=16.0)
    strong, truth = moving_ball_frames(3.0, step=16.0)
    assert MIN_CONTRAST_FACTOR * MOTION_DIFF_FLOOR > 0
    assert nearest(detect_middle(strong), truth) <= 0.75
    found = detect_middle(faint)
    assert all(float(r["contrast"]) >= MIN_CONTRAST_FACTOR * MOTION_DIFF_FLOOR for r in found)
