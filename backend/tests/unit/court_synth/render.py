"""In-memory pixel renderer for synthetic court scenes (design 11.1). Test-only.

Nothing is written to disk: frames are generated one at a time in memory.

* Court (hard blue, hard green, clay) with lines as projected ground quads,
  drawn at 4x and downsampled with area averaging (anti-aliasing).
* T2 render cost (brief): the static court and net are rendered ONCE per
  renderer at 4x; each frame composites only small patches over a copy.
* Players are dark capsules. Balls are integrated over 8 sub-frames of the
  exposure (motion blur), each sub-frame placed under the rolling shutter.
* Degradations: sensor noise (sigma 3 for T2; drawn once per renderer as a
  seeded bank and cropped at a random offset per frame), slow illumination drift,
  per-frame shake, PTS jitter, and an in-memory JPEG round trip at quality
  60-85 as a ROUGH compression stand-in. It is NOT H.264; results that use it
  must say so.

The net is drawn as a translucent band; the ball is always drawn in front of
it, so a far-half ball is never dimmed by the mesh as it would be on court.

Determinism: every random draw comes from ``np.random.default_rng`` seeded by
(seed, purpose, frame index), so the same seed gives byte-identical frames.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.court_model import (
    DOUBLES_HALF_WIDTH_M,
    HALF_LENGTH_M,
    CourtLine,
    NET_POST_HALF_SPAN_M,
    court_line_endpoints,
)
from tests.unit.court_synth.camera import NEAR_PLANE_M, SynthCamera, shake_offsets_px
from tests.unit.court_synth.physics import net_height_m
from tests.unit.court_synth.scene import BallTruth, PlayerTrack, SceneTruth

U8Array = npt.NDArray[np.uint8]
Float64Array = npt.NDArray[np.float64]

SUPERSAMPLE: Final[int] = 4
SUBFRAMES: Final[int] = 8
T2_NOISE_SIGMA: Final[float] = 3.0
T2_DRIFT_AMPLITUDE: Final[float] = 0.03
DRIFT_PERIOD_S: Final[float] = 20.0
T2_JPEG_QUALITY_RANGE: Final[tuple[int, int]] = (60, 85)
LINE_WIDTH_M: Final[float] = 0.05
BASELINE_WIDTH_M: Final[float] = 0.08
RUNOFF_SIDE_M: Final[float] = 3.66
RUNOFF_END_M: Final[float] = 6.40
GROUND_EXTENT_M: Final[float] = 80.0
PLAYER_HEIGHT_M: Final[float] = 1.8
PLAYER_RADIUS_M: Final[float] = 0.3
NET_ALPHA: Final[float] = 0.35
NET_TAPE_M: Final[float] = 0.05
NOISE_BANKS: Final[int] = 2
NOISE_BANK_MARGIN_PX: Final[int] = 64
_SHIFT_BITS: Final[int] = 4
_EDGE_SUBDIVISIONS: Final[int] = 48

LINE_BGR: Final[tuple[int, int, int]] = (235, 235, 235)
BACKGROUND_BGR: Final[tuple[int, int, int]] = (50, 70, 52)
PLAYER_BGR: Final[tuple[int, int, int]] = (38, 36, 40)
NET_BGR: Final[tuple[int, int, int]] = (25, 25, 25)


class Surface(StrEnum):
    HARD_BLUE = "hard_blue"
    HARD_GREEN = "hard_green"
    CLAY = "clay"


#: (court, surround) in BGR.
SURFACE_BGR: Final[dict[Surface, tuple[tuple[int, int, int], tuple[int, int, int]]]] = {
    Surface.HARD_BLUE: ((150, 95, 45), (70, 125, 65)),
    Surface.HARD_GREEN: ((75, 130, 60), (60, 100, 55)),
    Surface.CLAY: ((60, 100, 185), (70, 115, 170)),
}


@dataclass(frozen=True)
class RenderOptions:
    fps: float = 60.0
    surface: Surface = Surface.HARD_BLUE
    exposure_fraction: float = 0.5
    noise_sigma: float = 0.0
    drift_amplitude: float = 0.0
    jpeg_quality: int | None = None
    pts_jitter_s: float = 0.0
    shake_sigma_px: float = 0.0
    draw_players: bool = True

    @classmethod
    def clean(cls, fps: float = 60.0, surface: Surface = Surface.HARD_BLUE) -> RenderOptions:
        """T1: pixels, no degradation."""
        return cls(fps=fps, surface=surface)

    @classmethod
    def degraded(
        cls, seed: int, fps: float = 60.0, surface: Surface = Surface.HARD_BLUE
    ) -> RenderOptions:
        """T2: noise sigma 3, drift, and a seeded JPEG quality in 60-85."""
        rng = np.random.default_rng([seed, 0x7E2])
        quality = int(rng.integers(T2_JPEG_QUALITY_RANGE[0], T2_JPEG_QUALITY_RANGE[1] + 1))
        return cls(
            fps=fps,
            surface=surface,
            noise_sigma=T2_NOISE_SIGMA,
            drift_amplitude=T2_DRIFT_AMPLITUDE,
            jpeg_quality=quality,
        )


def _subdivide(polygon_xyz: Float64Array, parts: int) -> Float64Array:
    points = []
    count = len(polygon_xyz)
    for index in range(count):
        a, b = polygon_xyz[index], polygon_xyz[(index + 1) % count]
        for step in range(parts):
            points.append(a + (b - a) * (step / parts))
    return np.asarray(points, dtype=np.float64)


def _clip_near(polygon_camera: Float64Array) -> Float64Array:
    """Sutherland-Hodgman clip of a camera-frame polygon to z >= near plane."""
    clipped: list[Float64Array] = []
    count = len(polygon_camera)
    for index in range(count):
        a, b = polygon_camera[index], polygon_camera[(index + 1) % count]
        a_in, b_in = a[2] >= NEAR_PLANE_M, b[2] >= NEAR_PLANE_M
        if a_in:
            clipped.append(a)
        if a_in != b_in:
            t = (NEAR_PLANE_M - a[2]) / (b[2] - a[2])
            clipped.append(a + t * (b - a))
    return np.asarray(clipped, dtype=np.float64).reshape(-1, 3)


def _fill_world_polygon(
    canvas: U8Array, camera: SynthCamera, polygon_xyz: Float64Array, colour: tuple[int, int, int]
) -> None:
    """Fill a planar world polygon onto a SUPERSAMPLE canvas, sub-pixel exact."""
    camera_points = _clip_near(camera.to_camera(_subdivide(polygon_xyz, _EDGE_SUBDIVISIONS)))
    if len(camera_points) < 3:
        return
    uv = camera.project_camera_points(camera_points)
    scaled = np.round(uv * SUPERSAMPLE * (1 << _SHIFT_BITS)).astype(np.int64)
    limit = 1 << 28  # keep int32 fillPoly arithmetic safe for far-off vertices
    scaled = np.clip(scaled, -limit, limit).astype(np.int32)
    cv2.fillPoly(canvas, [scaled.reshape(-1, 1, 2)], colour, cv2.LINE_8, _SHIFT_BITS)


def _rect(x0: float, x1: float, y0: float, y1: float, z: float = 0.0) -> Float64Array:
    return np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], dtype=np.float64)


def _line_quad(line: CourtLine) -> Float64Array:
    """A line as a ground quad, extending INWARD from its outer-edge endpoints."""
    (x0, y0), (x1, y1) = court_line_endpoints(line)
    if line is CourtLine.CENTRE_SERVICE_LINE:
        return _rect(-LINE_WIDTH_M / 2.0, LINE_WIDTH_M / 2.0, y0, y1)
    if y0 == y1:  # baseline or service line: widen toward the net
        width = BASELINE_WIDTH_M if "baseline" in line.value else LINE_WIDTH_M
        inner = y0 - np.sign(y0) * width
        return _rect(x0, x1, min(y0, inner), max(y0, inner))
    inner_x = x0 - np.sign(x0) * LINE_WIDTH_M  # sideline: widen toward the centre
    return _rect(min(x0, inner_x), max(x0, inner_x), y0, y1)


def render_static_court(camera: SynthCamera, surface: Surface) -> U8Array:
    """Background, ground, court surface, lines and net, once, at 4x."""
    width, height = camera.frame_size
    big = np.empty((height * SUPERSAMPLE, width * SUPERSAMPLE, 3), dtype=np.uint8)
    big[:] = BACKGROUND_BGR
    court_bgr, surround_bgr = SURFACE_BGR[surface]
    extent = GROUND_EXTENT_M
    _fill_world_polygon(big, camera, _rect(-extent, extent, -extent, extent), surround_bgr)
    court_x = DOUBLES_HALF_WIDTH_M + RUNOFF_SIDE_M
    court_y = HALF_LENGTH_M + RUNOFF_END_M
    _fill_world_polygon(big, camera, _rect(-court_x, court_x, -court_y, court_y), court_bgr)
    for line in CourtLine:
        _fill_world_polygon(big, camera, _line_quad(line), LINE_BGR)
    static = cv2.resize(big, (width, height), interpolation=cv2.INTER_AREA)

    # Net: a translucent vertical band plus a white tape, composited at 4x.
    net_big = np.zeros((height * SUPERSAMPLE, width * SUPERSAMPLE), dtype=np.uint8)
    tape_big = np.zeros_like(net_big)
    xs = np.linspace(-NET_POST_HALF_SPAN_M, NET_POST_HALF_SPAN_M, 33)
    tops = np.array([net_height_m(float(x)) for x in xs])
    band = np.vstack(
        [np.column_stack([xs, np.zeros_like(xs), np.zeros_like(xs)]),
         np.column_stack([xs[::-1], np.zeros_like(xs), tops[::-1]])]
    )
    tape = np.vstack(
        [np.column_stack([xs, np.zeros_like(xs), tops - NET_TAPE_M]),
         np.column_stack([xs[::-1], np.zeros_like(xs), tops[::-1]])]
    )
    _fill_world_polygon(net_big, camera, band, (255, 255, 255))
    _fill_world_polygon(tape_big, camera, tape, (255, 255, 255))
    net_alpha = cv2.resize(net_big, (width, height), interpolation=cv2.INTER_AREA)
    tape_alpha = cv2.resize(tape_big, (width, height), interpolation=cv2.INTER_AREA)
    out = static.astype(np.float32)
    alpha = (net_alpha.astype(np.float32) / 255.0 * NET_ALPHA)[:, :, None]
    out = out * (1.0 - alpha) + np.array(NET_BGR, dtype=np.float32) * alpha
    tape_a = (tape_alpha.astype(np.float32) / 255.0)[:, :, None]
    out = out * (1.0 - tape_a) + np.array(LINE_BGR, dtype=np.float32) * tape_a
    return np.asarray(np.clip(np.rint(out), 0, 255).astype(np.uint8))


def frame_times_s(duration_s: float, fps: float, jitter_s: float, seed: int) -> Float64Array:
    """Frame start times; jitter is clipped under half a frame, so PTS stay
    strictly increasing."""
    count = int(np.floor(duration_s * fps)) + 1
    times = np.arange(count, dtype=np.float64) / fps
    if jitter_s > 0.0:
        rng = np.random.default_rng([seed, 0x9175])
        limit = 0.4 / fps
        times = times + np.clip(rng.normal(0.0, jitter_s, count), -limit, limit)
    return np.asarray(times, dtype=np.float64)


class CourtRenderer:
    """Renders one scene through one camera. Static layers are built once."""

    def __init__(
        self, scene: SceneTruth, camera: SynthCamera, options: RenderOptions, seed: int
    ) -> None:
        self.scene = scene
        self.camera = camera
        self.options = options
        self.seed = seed
        self.static = render_static_court(camera, options.surface)
        self.frame_times = frame_times_s(scene.duration_s, options.fps, options.pts_jitter_s, seed)
        self.shake = shake_offsets_px(seed, len(self.frame_times), options.shake_sigma_px)
        self._noise_banks = self._build_noise_banks() if options.noise_sigma > 0.0 else ()

    def _build_noise_banks(self) -> tuple[tuple[U8Array, U8Array], ...]:
        """Rounded Gaussian fields, split into positive and negative parts so a
        frame takes them with two saturating uint8 ops. A frame uses one bank at
        a seeded random crop offset: i.i.d. in space, decorrelated across
        frames, and ~15x cheaper than drawing 6M normals per 1080p frame."""
        width, height = self.camera.frame_size
        rng = np.random.default_rng([self.seed, 0x401E])
        shape = (height + NOISE_BANK_MARGIN_PX, width + NOISE_BANK_MARGIN_PX, 3)
        banks = []
        for _ in range(NOISE_BANKS):
            field = np.rint(rng.standard_normal(shape) * self.options.noise_sigma)
            banks.append(
                (
                    np.clip(field, 0, 255).astype(np.uint8),
                    np.clip(-field, 0, 255).astype(np.uint8),
                )
            )
        return tuple(banks)

    def _apply_noise(self, frame: U8Array, index: int) -> U8Array:
        height, width = frame.shape[:2]
        rng = np.random.default_rng([self.seed, 0x401F, index])
        bank = int(rng.integers(len(self._noise_banks)))
        dy, dx = (int(v) for v in rng.integers(0, NOISE_BANK_MARGIN_PX + 1, 2))
        positive, negative = self._noise_banks[bank]
        frame = cv2.add(frame, np.ascontiguousarray(positive[dy : dy + height, dx : dx + width]))
        return np.asarray(
            cv2.subtract(frame, np.ascontiguousarray(negative[dy : dy + height, dx : dx + width])),
            dtype=np.uint8,
        )

    @property
    def frame_count(self) -> int:
        return len(self.frame_times)

    @property
    def exposure_s(self) -> float:
        return self.options.exposure_fraction / self.options.fps

    def ball_uv(self, index: int, ball: BallTruth) -> Float64Array:
        """True ball centre pixel at mid-exposure (rolling shutter applied), or
        NaN when hidden, outside its flight, or behind the camera."""
        start = float(self.frame_times[index]) + 0.5 * self.exposure_s
        uv, time_s = self.camera.project_moving(
            lambda t: ball.trajectory.position_at(t), start
        )
        if not (ball.trajectory.start_s <= time_s <= ball.trajectory.end_s) or not (
            self.scene.ball_visible(ball, time_s)
        ):
            return np.array([np.nan, np.nan])
        return uv + self.shake[index]

    def _draw_player(self, frame: U8Array, track: PlayerTrack, time_s: float) -> None:
        x, y = track.position_at(time_s)
        ends = np.array([[x, y, 0.0], [x, y, PLAYER_HEIGHT_M]])
        uv = self.camera.project(ends)
        if not np.all(np.isfinite(uv)):
            return
        depth = float(self.camera.depth(ends[:1])[0])
        thickness = max(1, int(round(2.0 * PLAYER_RADIUS_M * self.camera.focal_px / depth)))
        scaled = np.round(uv * (1 << _SHIFT_BITS)).astype(np.int32)
        cv2.line(
            frame,
            (int(scaled[0, 0]), int(scaled[0, 1])),
            (int(scaled[1, 0]), int(scaled[1, 1])),
            PLAYER_BGR,
            thickness,
            cv2.LINE_AA,
            _SHIFT_BITS,
        )

    def _composite_ball(self, frame: U8Array, ball: BallTruth, frame_start_s: float) -> None:
        """Exposure integration over SUBFRAMES, rendered at 4x in a local patch."""
        discs: list[tuple[float, float, float]] = []
        for sub in range(SUBFRAMES):
            start = frame_start_s + (sub + 0.5) / SUBFRAMES * self.exposure_s
            uv, time_s = self.camera.project_moving(
                lambda t: ball.trajectory.position_at(t), start
            )
            if not (ball.trajectory.start_s <= time_s <= ball.trajectory.end_s):
                continue
            if not self.scene.ball_visible(ball, time_s) or not np.all(np.isfinite(uv)):
                continue
            position = ball.trajectory.position_at(time_s)
            radius = float(self.camera.radius_px(position, ball.radius_m)[0])
            discs.append((float(uv[0]), float(uv[1]), radius))
        if not discs:
            return
        height, width = frame.shape[:2]
        margin = 2.0
        x0 = int(np.floor(min(u - r for u, _, r in discs) - margin))
        y0 = int(np.floor(min(v - r for _, v, r in discs) - margin))
        x1 = int(np.ceil(max(u + r for u, _, r in discs) + margin))
        y1 = int(np.ceil(max(v + r for _, v, r in discs) + margin))
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, width), min(y1, height)
        if x1 <= x0 or y1 <= y0:
            return
        patch_w, patch_h = x1 - x0, y1 - y0
        coverage = np.zeros((patch_h * SUPERSAMPLE, patch_w * SUPERSAMPLE), dtype=np.float32)
        disc = np.zeros_like(coverage, dtype=np.uint8)
        unit = SUPERSAMPLE * (1 << _SHIFT_BITS)
        for u, v, r in discs:
            disc[:] = 0
            centre = (int(round((u - x0) * unit)), int(round((v - y0) * unit)))
            cv2.circle(disc, centre, int(round(r * unit)), 255, -1, cv2.LINE_8, _SHIFT_BITS)
            coverage += disc
        coverage /= 255.0 * SUBFRAMES
        alpha = cv2.resize(coverage, (patch_w, patch_h), interpolation=cv2.INTER_AREA)[:, :, None]
        patch = frame[y0:y1, x0:x1].astype(np.float32)
        blended = patch * (1.0 - alpha) + np.array(ball.colour_bgr, dtype=np.float32) * alpha
        frame[y0:y1, x0:x1] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)

    def render_frame(self, index: int) -> U8Array:
        options = self.options
        frame_start = float(self.frame_times[index])
        frame = self.static.copy()
        mid = frame_start + 0.5 * self.exposure_s
        if options.draw_players:
            for track in self.scene.players:
                self._draw_player(frame, track, mid)
        for ball in self.scene.balls:
            if ball.trajectory.start_s - self.exposure_s <= frame_start <= ball.trajectory.end_s:
                self._composite_ball(frame, ball, frame_start)
        dx, dy = self.shake[index]
        if dx != 0.0 or dy != 0.0:
            matrix = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float64)
            frame = cv2.warpAffine(
                frame, matrix, (frame.shape[1], frame.shape[0]), borderMode=cv2.BORDER_REPLICATE
            )
        if options.drift_amplitude != 0.0:
            gain = 1.0 + options.drift_amplitude * np.sin(2.0 * np.pi * mid / DRIFT_PERIOD_S)
            table = np.clip(np.rint(np.arange(256) * gain), 0, 255).astype(np.uint8)
            frame = cv2.LUT(frame, table)
        if self._noise_banks:
            frame = self._apply_noise(frame, index)
        if options.jpeg_quality is not None:
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, options.jpeg_quality])
            if not ok:
                raise RuntimeError("in-memory JPEG encode failed")
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        return np.asarray(frame, dtype=np.uint8)

    def iter_frames(self) -> Iterator[tuple[float, U8Array]]:
        for index in range(self.frame_count):
            yield float(self.frame_times[index]), self.render_frame(index)
