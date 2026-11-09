"""Synthetic pinhole camera for the court renderer (design 11.1). Test-only.

Built from focal length, position and a look-at target. Options: radial
distortion k1 (OpenCV-style, on the focal-normalised radius), rolling shutter
(row-dependent capture time) and per-frame shake (a whole-image pixel
translation, the small-rotation approximation).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final

import numpy as np
import numpy.typing as npt

Float64Array = npt.NDArray[np.float64]

#: Design 2.2: main lens, 65 degree horizontal field of view; tan(32.5 deg).
HALF_FOV_TAN: Final[float] = 0.637
#: Design 2.3 placement: centred, 6 m behind the near baseline, 3.5 m high.
DEFAULT_POSITION_M: Final[tuple[float, float, float]] = (0.0, -17.885, 3.5)
#: Aimed so the near singles corners and both Ts are on a 16:9 frame.
DEFAULT_TARGET_M: Final[tuple[float, float, float]] = (0.0, -1.0, 0.0)
#: Points closer than this in front of the camera are not projected.
NEAR_PLANE_M: Final[float] = 0.1
_ROLLING_SHUTTER_ITERATIONS: Final[int] = 4


def focal_for_width(width_px: int) -> float:
    """f = (W / 2) / tan(32.5 deg): 1005 px at 1280, 1507 px at 1920."""
    return (width_px / 2.0) / HALF_FOV_TAN


@dataclass(frozen=True, eq=False)
class SynthCamera:
    focal_px: float
    frame_size: tuple[int, int]
    position_m: tuple[float, float, float]
    #: Rows are camera right, down and forward in court axes.
    rotation: Float64Array
    k1: float = 0.0
    #: Time to read the sensor top to bottom; 0 is a global shutter.
    readout_s: float = 0.0

    @property
    def translation(self) -> Float64Array:
        return np.asarray(-self.rotation @ np.asarray(self.position_m), dtype=np.float64)

    @property
    def intrinsics(self) -> Float64Array:
        width, height = self.frame_size
        return np.array(
            [[self.focal_px, 0.0, width / 2.0], [0.0, self.focal_px, height / 2.0], [0.0, 0.0, 1.0]]
        )

    @property
    def projection(self) -> Float64Array:
        """Undistorted 3x4 ``K [R | t]``."""
        return np.asarray(
            self.intrinsics @ np.column_stack([self.rotation, self.translation]), dtype=np.float64
        )

    def ground_homography(self) -> Float64Array:
        """Undistorted court (x, y, z=0) -> image homography, h33 = 1."""
        h = self.projection[:, [0, 1, 3]]
        return np.asarray(h / h[2, 2], dtype=np.float64)

    def to_camera(self, xyz: npt.ArrayLike) -> Float64Array:
        points = np.atleast_2d(np.asarray(xyz, dtype=np.float64))
        return np.asarray(points @ self.rotation.T + self.translation, dtype=np.float64)

    def depth(self, xyz: npt.ArrayLike) -> Float64Array:
        return self.to_camera(xyz)[:, 2]

    def project(self, xyz: npt.ArrayLike) -> Float64Array:
        """(N, 3) court metres -> (N, 2) pixels, with distortion. Points behind
        the near plane come back as NaN."""
        return self.project_camera_points(self.to_camera(xyz))

    def project_camera_points(self, camera_xyz: npt.ArrayLike) -> Float64Array:
        """(N, 3) camera-frame points -> (N, 2) pixels, with distortion."""
        camera = np.atleast_2d(np.asarray(camera_xyz, dtype=np.float64))
        z = camera[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            normalised = camera[:, :2] / z[:, None]
        if self.k1 != 0.0:
            r2 = np.sum(normalised**2, axis=1, keepdims=True)
            normalised = normalised * (1.0 + self.k1 * r2)
        width, height = self.frame_size
        uv = normalised * self.focal_px + np.array([width / 2.0, height / 2.0])
        uv[z < NEAR_PLANE_M] = np.nan
        return np.asarray(uv, dtype=np.float64)

    def radius_px(self, xyz: npt.ArrayLike, radius_m: float) -> Float64Array:
        """Apparent radius of a sphere of ``radius_m`` centred at each point."""
        return np.asarray(self.focal_px * radius_m / self.depth(xyz), dtype=np.float64)

    def row_time_offset_s(self, v_px: float) -> float:
        """Capture time of image row ``v_px`` after the frame's first row."""
        height = self.frame_size[1]
        return self.readout_s * min(max(v_px, 0.0), float(height)) / float(height)

    def project_moving(
        self, position_at: Callable[[float], Float64Array], frame_start_s: float
    ) -> tuple[Float64Array, float]:
        """Project a moving point under the rolling shutter.

        The row a point lands on sets the time it is captured, which moves the
        point; a few fixed-point iterations settle it. Returns (uv, time).
        """
        time_s = frame_start_s
        uv = self.project(position_at(time_s))[0]
        if self.readout_s == 0.0:
            return uv, time_s
        for _ in range(_ROLLING_SHUTTER_ITERATIONS):
            if not np.all(np.isfinite(uv)):
                break
            time_s = frame_start_s + self.row_time_offset_s(float(uv[1]))
            uv = self.project(position_at(time_s))[0]
        return uv, time_s

    def with_options(self, *, k1: float | None = None, readout_s: float | None = None) -> SynthCamera:
        return replace(
            self,
            k1=self.k1 if k1 is None else k1,
            readout_s=self.readout_s if readout_s is None else readout_s,
        )


def look_at(
    *,
    focal_px: float,
    frame_size: tuple[int, int],
    position_m: tuple[float, float, float] = DEFAULT_POSITION_M,
    target_m: tuple[float, float, float] = DEFAULT_TARGET_M,
    roll_deg: float = 0.0,
    k1: float = 0.0,
    readout_s: float = 0.0,
) -> SynthCamera:
    position = np.asarray(position_m, dtype=np.float64)
    forward = np.asarray(target_m, dtype=np.float64) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    roll = math.radians(roll_deg)
    right, down = (
        math.cos(roll) * right + math.sin(roll) * down,
        -math.sin(roll) * right + math.cos(roll) * down,
    )
    return SynthCamera(
        focal_px=focal_px,
        frame_size=frame_size,
        position_m=(float(position[0]), float(position[1]), float(position[2])),
        rotation=np.vstack([right, down, forward]),
        k1=k1,
        readout_s=readout_s,
    )


def default_camera(frame_size: tuple[int, int] = (1920, 1080)) -> SynthCamera:
    """Section 2.3 placement with the 65 degree lens at this frame width."""
    return look_at(focal_px=focal_for_width(frame_size[0]), frame_size=frame_size)


def shake_offsets_px(seed: int, frame_count: int, sigma_px: float) -> Float64Array:
    """Seeded per-frame whole-image offsets, (N, 2). Zero when sigma is 0."""
    if sigma_px <= 0.0:
        return np.zeros((frame_count, 2))
    rng = np.random.default_rng([seed, 0x5A4E])
    return np.asarray(rng.normal(0.0, sigma_px, (frame_count, 2)), dtype=np.float64)
