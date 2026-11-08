"""Optional court fit from line segments (SESSION_MODE_PHASE2.md step 19). PURE.

An ASSIST, never a replacement for taps: every candidate is shown to the user
to confirm, and the taps it proposes then go through ``calibrate_court`` like
hand taps. Segments come from ``app/ball/court_lines.py``.

1. Segments split into two direction families: across-court lines (baselines,
   service lines) are near-horizontal from a camera behind a baseline;
   sidelines are steeper.
2. Collinear segments merge into lines; the 6 longest per family are kept,
   so there are at most 15 x 15 = 225 line-pair hypotheses.
3. Each hypothesis (two across, two along) is tried against every ordered
   assignment to model lines, giving four corner correspondences and a
   homography.
4. Score: the share of visible model-line length lying within 3 px of a
   detected segment. Accept support >= ``MIN_SUPPORT``, at least 6 model
   lines visible, a non-collapsed fit, and a plausible camera.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from app.analysis.court_calibration import CourtTap
from app.analysis.court_model import court_line_endpoints, keypoint_xy
from app.analysis.homography import apply_homography, decompose_camera_pose, fit_homography_dlt
from app.models.court import CourtKeypoint, CourtLine

Float64Array = npt.NDArray[np.float64]

COURT_FIT_VERSION: Final[str] = "court_fit_v1"
#: Lines within this many degrees of horizontal are across-court candidates.
ACROSS_MAX_ANGLE_DEG: Final[float] = 25.0
#: Steeper than this are sideline candidates.
ALONG_MIN_ANGLE_DEG: Final[float] = 30.0
#: Both edges of one painted line come back from Hough a few px apart; they
#: must merge, or a hypothesis pairs a line with itself (a degenerate fit that
#: squashes the court onto one line and still scores support 1.0).
MERGE_DISTANCE_PX: Final[float] = 6.0
#: Projected keypoints closer than this, or a corner quadrilateral smaller than
#: this share of the frame, is a collapsed fit.
MIN_KEYPOINT_SEPARATION_PX: Final[float] = 6.0
MIN_QUAD_AREA_FRACTION: Final[float] = 0.02
MERGE_ANGLE_DEG: Final[float] = 2.0
LINES_PER_FAMILY: Final[int] = 6
SUPPORT_DISTANCE_PX: Final[float] = 3.0
#: A sample is supported only by a segment running within this angle of the
#: projected model line: otherwise dense long clutter supports any court
#: (300 random segments gave support 0.97).
SUPPORT_ANGLE_DEG: Final[float] = 5.0
#: Design 0.6, raised from real-clip negatives (first frames, 2026-11-08): a
#: busy courtless frame (serve_02_4902143, 1,390 segments) produced false fits
#: at 0.66-0.67; synthetic courts fit at 1.0. Placeholder until owner footage.
MIN_SUPPORT: Final[float] = 0.72
#: Only the longest segments are scored: texture (thousands of short
#: segments) otherwise buys support at random, and costs minutes per frame.
MAX_SEGMENTS: Final[int] = 400
MIN_LINES_VISIBLE: Final[int] = 6
SAMPLES_PER_LINE: Final[int] = 24
MIN_CONDITION: Final[float] = 1e-3
CAMERA_HEIGHT_RANGE_M: Final[tuple[float, float]] = (0.5, 40.0)
#: Two candidates whose projected corners all lie within this are one fit.
DUPLICATE_PX: Final[float] = 10.0

# Model lines by direction, as constant court coordinates.
_ACROSS_Y: Final[tuple[float, ...]] = tuple(
    sorted({court_line_endpoints(l)[0][1] for l in (
        CourtLine.NEAR_BASELINE, CourtLine.NEAR_SERVICE_LINE,
        CourtLine.FAR_SERVICE_LINE, CourtLine.FAR_BASELINE,
    )}, reverse=True)
)  # far first: far lines appear higher in the image
_ALONG_X: Final[tuple[float, ...]] = tuple(
    sorted({court_line_endpoints(l)[0][0] for l in (
        CourtLine.LEFT_DOUBLES_SIDELINE, CourtLine.LEFT_SINGLES_SIDELINE,
        CourtLine.RIGHT_SINGLES_SIDELINE, CourtLine.RIGHT_DOUBLES_SIDELINE,
    )})
)  # left first


@dataclass(frozen=True)
class CourtFitCandidate:
    taps: tuple[CourtTap, ...]
    support: float
    lines_visible: int


@dataclass(frozen=True, eq=False)
class _Line:
    """A merged image line: unit normal n, offset c (n . p = c), and the
    segments that support it."""

    normal: Float64Array
    offset: float
    length: float
    angle_deg: float


def _angle_deg(segments: Float64Array) -> Float64Array:
    dx = segments[:, 2] - segments[:, 0]
    dy = segments[:, 3] - segments[:, 1]
    return np.degrees(np.arctan2(dy, dx)) % 180.0


def _fit_line(points: Float64Array, weights: Float64Array) -> tuple[Float64Array, float]:
    """Weighted total least squares line through points: (unit normal, offset)."""
    w = weights / weights.sum()
    mean = (points * w[:, None]).sum(axis=0)
    centred = (points - mean) * np.sqrt(w)[:, None]
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    normal = vt[-1]
    return normal, float(normal @ mean)


def merge_lines(segments: Float64Array) -> list[_Line]:
    """Greedy collinear merge, longest segment first. Deterministic."""
    if len(segments) == 0:
        return []
    lengths = np.hypot(segments[:, 2] - segments[:, 0], segments[:, 3] - segments[:, 1])
    angles = _angle_deg(segments)
    order = np.lexsort((np.arange(len(segments)), -lengths))
    groups: list[list[int]] = []
    fits: list[tuple[Float64Array, float, float]] = []
    for index in order:
        p = segments[index, :2]
        q = segments[index, 2:]
        placed = False
        for group, (normal, offset, angle) in zip(groups, fits, strict=True):
            diff = abs(angles[index] - angle) % 180.0
            if min(diff, 180.0 - diff) > MERGE_ANGLE_DEG:
                continue
            if abs(normal @ p - offset) <= MERGE_DISTANCE_PX and abs(normal @ q - offset) <= MERGE_DISTANCE_PX:
                group.append(int(index))
                placed = True
                break
        if not placed:
            groups.append([int(index)])
            normal, offset = _fit_line(np.vstack([p, q]), np.array([1.0, 1.0]))
            fits.append((normal, offset, float(angles[index])))
    lines: list[_Line] = []
    for group in groups:
        pts = np.vstack([segments[group, :2], segments[group, 2:]])
        weights = np.concatenate([lengths[group], lengths[group]])
        normal, offset = _fit_line(pts, weights)
        lines.append(
            _Line(
                normal=normal,
                offset=offset,
                length=float(lengths[group].sum()),
                angle_deg=float(np.degrees(np.arctan2(normal[0], -normal[1])) % 180.0),
            )
        )
    return sorted(lines, key=lambda l: (-l.length, l.offset))


def _intersect(a: _Line, b: _Line) -> Float64Array | None:
    m = np.vstack([a.normal, b.normal])
    if abs(np.linalg.det(m)) < 1e-9:
        return None
    return np.linalg.solve(m, np.array([a.offset, b.offset]))


def _families(lines: list[_Line]) -> tuple[list[_Line], list[_Line]]:
    across, along = [], []
    for line in lines:
        tilt = min(line.angle_deg, 180.0 - line.angle_deg)
        if tilt <= ACROSS_MAX_ANGLE_DEG:
            across.append(line)
        elif tilt >= ALONG_MIN_ANGLE_DEG:
            along.append(line)
    return across[:LINES_PER_FAMILY], along[:LINES_PER_FAMILY]


def _point_segment_distance(points: Float64Array, segments: Float64Array) -> Float64Array:
    """(P,) distance from each point to its nearest segment."""
    a = segments[None, :, :2]
    b = segments[None, :, 2:]
    p = points[:, None, :]
    ab = b - a
    denom = np.maximum((ab * ab).sum(axis=2), 1e-12)
    t = np.clip(((p - a) * ab).sum(axis=2) / denom, 0.0, 1.0)
    closest = a + t[..., None] * ab
    return np.sqrt(((p - closest) ** 2).sum(axis=2)).min(axis=1)


def _score(
    h_ci: Float64Array, segments: Float64Array, frame_size: tuple[int, int]
) -> tuple[float, int, float]:
    """(support, lines visible): share of in-frame model-line samples within
    ``SUPPORT_DISTANCE_PX`` of a segment, and lines with half their length in
    frame."""
    width, height = frame_size
    fractions = np.linspace(0.0, 1.0, SAMPLES_PER_LINE)
    seg_angles = _angle_deg(segments)
    distances: list[Float64Array] = []
    visible = 0
    for line in CourtLine:
        (x0, y0), (x1, y1) = court_line_endpoints(line)
        court = np.column_stack([x0 + (x1 - x0) * fractions, y0 + (y1 - y0) * fractions])
        image = apply_homography(h_ci, court)
        inside = (
            np.all(np.isfinite(image), axis=1)
            & (image[:, 0] >= 0) & (image[:, 0] < width)
            & (image[:, 1] >= 0) & (image[:, 1] < height)
        )
        if inside.mean() >= 0.5:
            visible += 1
        points = image[inside]
        if len(points) == 0:
            continue
        ends = image[[0, -1]]
        line_angle = math.degrees(math.atan2(ends[1, 1] - ends[0, 1], ends[1, 0] - ends[0, 0])) % 180.0
        diff = np.abs(seg_angles - line_angle) % 180.0
        direction = (ends[1] - ends[0]) / max(float(np.hypot(*(ends[1] - ends[0]))), 1e-9)
        normal = np.array([-direction[1], direction[0]])
        offset = float(normal @ ends[0])
        # Collinear: both endpoints ON the projected line, and parallel to it.
        # A long clutter line crossing at a shallow angle passes near it but
        # its endpoints do not (300 random lines: support 0.73 without this).
        on_line = (
            (np.abs(segments[:, :2] @ normal - offset) <= SUPPORT_DISTANCE_PX)
            & (np.abs(segments[:, 2:] @ normal - offset) <= SUPPORT_DISTANCE_PX)
        )
        parallel = segments[on_line & (np.minimum(diff, 180.0 - diff) <= SUPPORT_ANGLE_DEG)]
        if len(parallel) == 0:
            distances.append(np.full(len(points), math.inf))
        else:
            distances.append(_point_segment_distance(points, parallel))
    if not distances:
        return 0.0, visible, math.inf
    distance = np.concatenate(distances)
    supported = distance <= SUPPORT_DISTANCE_PX
    residual = float(distance[supported].mean()) if supported.any() else math.inf
    return float(supported.mean()), visible, residual


_ALL_KEYPOINTS: Final[Float64Array] = np.array([keypoint_xy(k) for k in CourtKeypoint])
_DOUBLES_CORNERS: Final[Float64Array] = np.array(
    [(-5.485, -11.885), (5.485, -11.885), (5.485, 11.885), (-5.485, 11.885)]
)


def _collapsed(h_ci: Float64Array, frame_size: tuple[int, int]) -> bool:
    """True for a degenerate fit: keypoints on top of each other, or a court
    whose image quadrilateral is a sliver."""
    points = apply_homography(h_ci, _ALL_KEYPOINTS)
    if not np.all(np.isfinite(points)):
        return True
    gaps = np.hypot(*(points[:, None, :] - points[None, :, :]).transpose(2, 0, 1))
    np.fill_diagonal(gaps, np.inf)
    if float(gaps.min()) < MIN_KEYPOINT_SEPARATION_PX:
        return True
    quad = apply_homography(h_ci, _DOUBLES_CORNERS)
    x, y = quad[:, 0], quad[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return area < MIN_QUAD_AREA_FRACTION * frame_size[0] * frame_size[1]


def _plausible(h_ci: Float64Array, frame_size: tuple[int, int]) -> bool:
    pose = decompose_camera_pose(h_ci, frame_size)
    if pose is None:
        return False
    low, high = CAMERA_HEIGHT_RANGE_M
    return low <= pose.height_m <= high and math.isfinite(pose.focal_px) and pose.focal_px > 0


def _taps(h_ci: Float64Array, frame_size: tuple[int, int]) -> tuple[CourtTap, ...]:
    """Proposed taps: the 14 keypoints projected, those inside the frame.

    Intersecting matched image lines instead was measured WORSE (worst tap
    4.7 -> 8.2 px on synthetic courts): at the far end the singles and
    doubles sidelines converge inside the merge distance, so merged image
    lines mix the two. The user confirms or adjusts every tap anyway.
    """
    width, height = frame_size
    taps: list[CourtTap] = []
    for keypoint in CourtKeypoint:
        u, v = apply_homography(h_ci, np.array([keypoint_xy(keypoint)]))[0]
        if math.isfinite(u) and math.isfinite(v) and 0 <= u < width and 0 <= v < height:
            taps.append(CourtTap(keypoint, float(u), float(v)))
    return tuple(taps)


def fit_court_candidates(
    segments: Float64Array, frame_size: tuple[int, int], *, max_candidates: int = 3
) -> tuple[CourtFitCandidate, ...]:
    """At most ``max_candidates`` accepted fits, best support first."""
    segs = np.asarray(segments, dtype=np.float64).reshape(-1, 4)
    if len(segs) > MAX_SEGMENTS:
        lengths = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
        segs = segs[np.lexsort((np.arange(len(segs)), -lengths))[:MAX_SEGMENTS]]
    merged = merge_lines(segs)
    across, along = _families(merged)
    if len(across) < 2 or len(along) < 2:
        return ()
    scored: list[tuple[float, int, float, Float64Array]] = []
    for top, bottom in itertools.combinations(across, 2):
        for left, right in itertools.combinations(along, 2):
            corners = [_intersect(h, v) for h in (top, bottom) for v in (left, right)]
            if any(c is None for c in corners):
                continue
            image = np.array(corners)
            # Order in the image: top row first (smaller y), left first.
            rows = sorted([image[0:2], image[2:4]], key=lambda r: float(r[:, 1].mean()))
            ordered = np.vstack([r[np.argsort(r[:, 0])] for r in rows])
            for y_top, y_bottom in itertools.combinations(_ACROSS_Y, 2):  # far first
                for x_left, x_right in itertools.combinations(_ALONG_X, 2):
                    court = np.array(
                        [[x_left, y_top], [x_right, y_top], [x_left, y_bottom], [x_right, y_bottom]]
                    )
                    h_ci, condition = fit_homography_dlt(court, ordered)
                    if not np.all(np.isfinite(h_ci)) or condition < MIN_CONDITION:
                        continue
                    if _collapsed(h_ci, frame_size):
                        continue
                    support, visible, residual = _score(h_ci, segs, frame_size)
                    if support >= MIN_SUPPORT and visible >= MIN_LINES_VISIBLE:
                        scored.append((support, visible, residual, h_ci))
    # Ties on support (common: several fits sit within 3 px) go to the fit
    # whose model lines sit closest to the detected ones.
    scored.sort(key=lambda s: (-round(s[0], 3), -s[1], s[2]))
    chosen: list[tuple[float, int, Float64Array]] = []
    for support, visible, _residual, h_ci in scored:
        if not _plausible(h_ci, frame_size):
            continue
        corners = apply_homography(
            h_ci, np.array([keypoint_xy(k) for k in (
                CourtKeypoint.NEAR_LEFT_DOUBLES_CORNER, CourtKeypoint.NEAR_RIGHT_DOUBLES_CORNER,
                CourtKeypoint.FAR_LEFT_DOUBLES_CORNER, CourtKeypoint.FAR_RIGHT_DOUBLES_CORNER,
            )])
        )
        duplicate = any(
            np.all(np.hypot(*(corners - apply_homography(other, np.array([keypoint_xy(k) for k in (
                CourtKeypoint.NEAR_LEFT_DOUBLES_CORNER, CourtKeypoint.NEAR_RIGHT_DOUBLES_CORNER,
                CourtKeypoint.FAR_LEFT_DOUBLES_CORNER, CourtKeypoint.FAR_RIGHT_DOUBLES_CORNER,
            )]))).T) <= DUPLICATE_PX)
            for _, _, other in chosen
        )
        if duplicate:
            continue
        chosen.append((support, visible, h_ci))
        if len(chosen) >= max_candidates:
            break
    return tuple(
        CourtFitCandidate(taps=_taps(h, frame_size), support=s, lines_visible=v)
        for s, v, h in chosen
    )
