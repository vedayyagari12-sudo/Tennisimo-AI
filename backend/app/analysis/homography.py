"""Ground-plane homography: normalised DLT, mapping, Jacobian, covariance, pose.

PURE numpy (SESSION_MODE_PHASE2.md 3.3). ``cv2.findHomography`` is banned in
``app/analysis`` and not needed.

Conventions: court points are metres on the ground (``court_model``), image
points are pixels with v down. ``h_ci`` maps court -> image, ``h_ic`` image ->
court. Every 3x3 returned here is scaled so h33 = 1.

DLT condition (brief G1): numpy returns singular values in DESCENDING order, so
the design's sigma7/sigma8 is always >= 1 and could never fire. The gate is
``s[7] / s[0]`` of the Hartley-normalised A: it is near zero when the solution
is not unique (degenerate tap geometry). ``full_matrices=True`` is required
because with 4 taps A is 8x9 and the null vector is the 9th row of Vt.

Camera model (3.3): square pixels, principal point at frame centre, so
``h_ci ~ K [r1 r2 t]``. f is solved by least squares over BOTH the
orthogonality and the equal-norm constraints, because a centred, unrolled
camera makes orthogonality vanish on its own (vanishing point at infinity).
``projection_matrix`` (brief G6) is ``K [r1 r2 r3 t]`` with ``r3 = r1 x r2``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

Float64Array = npt.NDArray[np.float64]

HOMOGRAPHY_VERSION: Final[str] = "homography_v1"

#: Minimum number of correspondences for a homography.
MIN_POINTS: Final[int] = 4
#: Central-difference step for the calibration covariance, pixels.
COVARIANCE_STEP_PX: Final[float] = 1e-2
#: Tennis ball radius, metres (ITF 6.54-6.86 cm diameter).
BALL_RADIUS_M: Final[float] = 0.0335
#: Depth-reweighting passes after the plain DLT (see ``fit_homography_dlt``).
#: One pass converges to the geometric optimum on synthetic taps; two is margin.
DLT_REWEIGHT_PASSES: Final[int] = 2
#: Below this |h33| the matrix is scaled by its norm instead of by h33.
_H33_EPSILON: Final[float] = 1e-12
#: Floor on a point's projective depth when reweighting.
_DEPTH_EPSILON: Final[float] = 1e-9


def _as_points(points: npt.ArrayLike) -> Float64Array:
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError(f"expected an (N, 2) array, got shape {array.shape}")
    return array


def _scaled(h: Float64Array) -> Float64Array:
    if abs(h[2, 2]) > _H33_EPSILON:
        return np.asarray(h / h[2, 2], dtype=np.float64)
    return np.asarray(h / np.linalg.norm(h), dtype=np.float64)


def normalization_transform(points: Float64Array) -> Float64Array:
    """Hartley 3x3: zero centroid, mean distance sqrt(2).

    Raises:
        ValueError: if every point coincides.
    """
    pts = _as_points(points)
    centroid = pts.mean(axis=0)
    mean_distance = float(np.mean(np.linalg.norm(pts - centroid, axis=1)))
    if mean_distance <= 0.0:
        raise ValueError("points are coincident")
    scale = math.sqrt(2.0) / mean_distance
    return np.array(
        [
            [scale, 0.0, -scale * centroid[0]],
            [0.0, scale, -scale * centroid[1]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def apply_homography(h: Float64Array, points: Float64Array) -> Float64Array:
    """Map (N, 2) points through a 3x3 homography."""
    pts = _as_points(points)
    homogeneous = np.column_stack([pts, np.ones(len(pts))]) @ np.asarray(h, dtype=np.float64).T
    return np.asarray(homogeneous[:, :2] / homogeneous[:, 2:3], dtype=np.float64)


def fit_homography_dlt(src: Float64Array, dst: Float64Array) -> tuple[Float64Array, float]:
    """Normalised DLT from ``src`` to ``dst``. Returns (H, condition).

    ``condition = s[7] / s[0]`` of the normalised, UNWEIGHTED A (brief G1);
    small means the tap geometry does not pin down a unique homography. The
    returned H then has ``DLT_REWEIGHT_PASSES`` depth-reweighting passes, so
    its residual is geometric error in ``dst`` units.

    Raises:
        ValueError: on mismatched shapes or fewer than 4 points.
    """
    source = _as_points(src)
    target = _as_points(dst)
    if source.shape != target.shape:
        raise ValueError("src and dst must have the same shape")
    if len(source) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} points, got {len(source)}")
    t_src = normalization_transform(source)
    t_dst = normalization_transform(target)
    s = apply_homography(t_src, source)
    d = apply_homography(t_dst, target)
    a = np.zeros((2 * len(s), 9), dtype=np.float64)
    x, y = s[:, 0], s[:, 1]
    u, v = d[:, 0], d[:, 1]
    a[0::2, 0], a[0::2, 1], a[0::2, 2] = -x, -y, -1.0
    a[0::2, 6], a[0::2, 7], a[0::2, 8] = u * x, u * y, u
    a[1::2, 3], a[1::2, 4], a[1::2, 5] = -x, -y, -1.0
    a[1::2, 6], a[1::2, 7], a[1::2, 8] = v * x, v * y, v
    _, singular, vt = np.linalg.svd(a, full_matrices=True)
    h_normalised = vt[-1].reshape(3, 3)
    condition = float(singular[7] / singular[0])
    # Algebraic DLT weights each point's residual by its projective depth w,
    # so far-court taps are fitted almost exactly and the error is pushed onto
    # near ones (geometric RMS about 2x optimal on a baseline view). Rows
    # reweighted by 1/w make the algebraic error the geometric one in dst.
    homogeneous_src = np.column_stack([s, np.ones(len(s))])
    for _ in range(DLT_REWEIGHT_PASSES):
        depth = np.abs(homogeneous_src @ h_normalised[2])
        weights = 1.0 / np.maximum(depth, _DEPTH_EPSILON)
        weights /= weights.mean()
        _, _, vt = np.linalg.svd(a * np.repeat(weights, 2)[:, None], full_matrices=True)
        h_normalised = vt[-1].reshape(3, 3)
    h = np.linalg.inv(t_dst) @ h_normalised @ t_src
    return _scaled(h), condition


def invert_homography(h: Float64Array) -> Float64Array:
    """Inverse homography, scaled so h33 = 1."""
    return _scaled(np.linalg.inv(np.asarray(h, dtype=np.float64)))


def image_to_court_jacobian(h_ic: Float64Array, uv: tuple[float, float]) -> Float64Array:
    """Analytic 2x2 d(X, Y) / d(u, v) of the image -> court map at ``uv``."""
    h = np.asarray(h_ic, dtype=np.float64)
    u, v = uv
    w = h[2, 0] * u + h[2, 1] * v + h[2, 2]
    big_x = (h[0, 0] * u + h[0, 1] * v + h[0, 2]) / w
    big_y = (h[1, 0] * u + h[1, 1] * v + h[1, 2]) / w
    return np.array(
        [
            [h[0, 0] - big_x * h[2, 0], h[0, 1] - big_x * h[2, 1]],
            [h[1, 0] - big_y * h[2, 0], h[1, 1] - big_y * h[2, 1]],
        ],
        dtype=np.float64,
    ) / w


def reprojection_errors_px(
    h_ci: Float64Array, court_xy: Float64Array, image_uv: Float64Array
) -> Float64Array:
    """Per-tap pixel distance between the projected court point and the tap."""
    projected = apply_homography(h_ci, _as_points(court_xy))
    return np.asarray(np.linalg.norm(projected - _as_points(image_uv), axis=1), dtype=np.float64)


def calibration_covariance(
    court_xy: Float64Array,
    image_uv: Float64Array,
    uv: tuple[float, float],
    sigma_tap_px: float,
) -> Float64Array:
    """2x2 ground covariance at ``uv`` caused by tap error (design 3.3).

    Central difference of the mapped point with respect to each tap coordinate
    gives the 2x2N matrix C; ``Sigma = sigma_tap^2 C C^T``. Deterministic, and
    keeps the correlations between X and Y.
    """
    court = _as_points(court_xy)
    taps = _as_points(image_uv)
    query = np.array([uv], dtype=np.float64)
    sensitivity = np.zeros((2, 2 * len(taps)), dtype=np.float64)
    for index in range(len(taps)):
        for axis in range(2):
            plus = taps.copy()
            minus = taps.copy()
            plus[index, axis] += COVARIANCE_STEP_PX
            minus[index, axis] -= COVARIANCE_STEP_PX
            h_plus, _ = fit_homography_dlt(plus, court)
            h_minus, _ = fit_homography_dlt(minus, court)
            delta = apply_homography(h_plus, query)[0] - apply_homography(h_minus, query)[0]
            sensitivity[:, 2 * index + axis] = delta / (2.0 * COVARIANCE_STEP_PX)
    return np.asarray(sigma_tap_px**2 * sensitivity @ sensitivity.T, dtype=np.float64)


@dataclass(frozen=True, eq=False)
class CameraPose:
    """Camera recovered from the ground homography. ``position_m`` is the camera
    centre in court metres; ``height_m`` is its z and ``foot_xy_m`` its (x, y).
    ``projection_matrix`` is the 3x4 ``K [R | t]`` (brief G6)."""

    focal_px: float
    height_m: float
    foot_xy_m: tuple[float, float]
    position_m: tuple[float, float, float]
    projection_matrix: Float64Array


def decompose_camera_pose(h_ci: Float64Array, frame_size: tuple[int, int]) -> CameraPose | None:
    """Recover f, R, t from ``h_ci`` for a ``(width, height)`` frame.

    Returns None when no positive f^2 satisfies the constraints (the homography
    cannot come from a square-pixel, centred-principal-point camera).
    Plausibility gating is ``court_calibration``'s job, not this function's.
    """
    width, height = frame_size
    cx, cy = width / 2.0, height / 2.0
    scale = float(max(width, height))
    to_normalised = np.array(
        [[1.0 / scale, 0.0, -cx / scale], [0.0, 1.0 / scale, -cy / scale], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    m = to_normalised @ np.asarray(h_ci, dtype=np.float64)
    m = m / np.linalg.norm(m)
    h1, h2, h3 = m[:, 0], m[:, 1], m[:, 2]
    # In normalised units, w = 1 / f_n^2. Each constraint is linear in w.
    a1 = h1[0] * h2[0] + h1[1] * h2[1]
    b1 = h1[2] * h2[2]
    a2 = h1[0] ** 2 + h1[1] ** 2 - h2[0] ** 2 - h2[1] ** 2
    b2 = h1[2] ** 2 - h2[2] ** 2
    denominator = a1 * a1 + a2 * a2
    if denominator <= 0.0:
        return None
    w = -(a1 * b1 + a2 * b2) / denominator
    if not w > 0.0:
        return None
    focal_normalised = 1.0 / math.sqrt(w)
    k_inverse = np.diag([1.0 / focal_normalised, 1.0 / focal_normalised, 1.0])
    g1, g2, g3 = k_inverse @ h1, k_inverse @ h2, k_inverse @ h3
    lam = 2.0 / (float(np.linalg.norm(g1)) + float(np.linalg.norm(g2)))
    if g3[2] * lam < 0.0:  # the court origin must be in front of the camera
        lam = -lam
    r1, r2, t = lam * g1, lam * g2, lam * g3
    rotation = np.column_stack([r1, r2, np.cross(r1, r2)])
    u_rot, _, vt_rot = np.linalg.svd(rotation)
    rotation = u_rot @ vt_rot
    if np.linalg.det(rotation) < 0.0:
        return None
    focal_px = focal_normalised * scale
    centre = -rotation.T @ t
    k = np.array([[focal_px, 0.0, cx], [0.0, focal_px, cy], [0.0, 0.0, 1.0]], dtype=np.float64)
    projection = k @ np.column_stack([rotation, t])
    return CameraPose(
        focal_px=float(focal_px),
        height_m=float(centre[2]),
        foot_xy_m=(float(centre[0]), float(centre[1])),
        position_m=(float(centre[0]), float(centre[1]), float(centre[2])),
        projection_matrix=np.asarray(projection, dtype=np.float64),
    )


def project_points_3d(pose: CameraPose, xyz: Float64Array) -> Float64Array:
    """Project (N, 3) court-metre points (z up) to (N, 2) pixels (brief G6)."""
    points = np.asarray(xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError(f"expected an (N, 3) array, got shape {points.shape}")
    homogeneous = np.column_stack([points, np.ones(len(points))]) @ pose.projection_matrix.T
    return np.asarray(homogeneous[:, :2] / homogeneous[:, 2:3], dtype=np.float64)


def correct_ball_centre_parallax(
    court_xy: tuple[float, float],
    pose: CameraPose,
    ball_radius_m: float = BALL_RADIUS_M,
) -> tuple[float, float]:
    """Ground contact point under a ball whose CENTRE maps to ``court_xy``.

    The ball centre sits ``ball_radius_m`` above the ground, so mapping its
    pixel through the ground homography lands on the ray's ground hit, beyond
    the ball. The true centre is where that camera ray is at height r.

    Raises:
        ValueError: if the camera is not above the ball centre.
    """
    cx, cy, cz = pose.position_m
    if cz <= ball_radius_m:
        raise ValueError("camera must be above the ball centre")
    fraction = (cz - ball_radius_m) / cz
    return (cx + fraction * (court_xy[0] - cx), cy + fraction * (court_xy[1] - cy))


def image_scale_px_per_m(pose: CameraPose, h_ic: Float64Array, uv: tuple[float, float]) -> float:
    """Pixels per metre, across the line of sight, at the ground point under ``uv``.

    ``f / range`` from the camera centre to that ground point: the scale of an
    object facing the camera there (a ball, a wrist offset). For an in-flight
    ball the ground point is beyond the ball, so this under-estimates its size.
    """
    ground = apply_homography(h_ic, np.array([uv], dtype=np.float64))[0]
    cx, cy, cz = pose.position_m
    distance = math.sqrt((ground[0] - cx) ** 2 + (ground[1] - cy) ** 2 + cz**2)
    return pose.focal_px / distance
