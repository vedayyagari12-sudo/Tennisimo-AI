"""Known-truth pinhole cameras for the homography and calibration tests.

Test-only. Builds an exact 3x4 projection from focal length, position and a
look-at target, so recovered homographies and poses can be checked against the
camera that made them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.analysis.court_model import CourtKeypoint, keypoint_xy

Float64Array = npt.NDArray[np.float64]

#: Section 2.3 placement: behind the near baseline, 6 m back, 3.5 m high.
DEFAULT_POSITION_M: tuple[float, float, float] = (0.0, -17.885, 3.5)
DEFAULT_TARGET_M: tuple[float, float, float] = (0.0, -1.0, 0.0)
#: f for a 65 degree horizontal field of view at 1920 px (design 2.2).
DEFAULT_FOCAL_1920_PX: float = 1507.0

RECOMMENDED_SIX: tuple[CourtKeypoint, ...] = (
    CourtKeypoint.NEAR_LEFT_SINGLES_CORNER,
    CourtKeypoint.NEAR_RIGHT_SINGLES_CORNER,
    CourtKeypoint.FAR_LEFT_SINGLES_CORNER,
    CourtKeypoint.FAR_RIGHT_SINGLES_CORNER,
    CourtKeypoint.NEAR_SERVICE_T,
    CourtKeypoint.FAR_SERVICE_T,
)


@dataclass(frozen=True, eq=False)
class TruthCamera:
    focal_px: float
    frame_size: tuple[int, int]
    position_m: tuple[float, float, float]
    rotation: Float64Array  # rows: camera right, down, forward in court axes
    projection: Float64Array  # 3x4

    @property
    def h_court_to_image(self) -> Float64Array:
        h = self.projection[:, [0, 1, 3]]
        return np.asarray(h / h[2, 2], dtype=np.float64)

    def project(self, xyz: Float64Array) -> Float64Array:
        points = np.asarray(xyz, dtype=np.float64)
        homogeneous = np.column_stack([points, np.ones(len(points))]) @ self.projection.T
        return np.asarray(homogeneous[:, :2] / homogeneous[:, 2:3], dtype=np.float64)

    def project_ground(self, xy: Float64Array) -> Float64Array:
        points = np.asarray(xy, dtype=np.float64)
        return self.project(np.column_stack([points, np.zeros(len(points))]))


def look_at_camera(
    *,
    focal_px: float = DEFAULT_FOCAL_1920_PX,
    frame_size: tuple[int, int] = (1920, 1080),
    position_m: tuple[float, float, float] = DEFAULT_POSITION_M,
    target_m: tuple[float, float, float] = DEFAULT_TARGET_M,
    roll_deg: float = 0.0,
) -> TruthCamera:
    position = np.asarray(position_m, dtype=np.float64)
    forward = np.asarray(target_m, dtype=np.float64) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    roll = np.deg2rad(roll_deg)
    right, down = (
        np.cos(roll) * right + np.sin(roll) * down,
        -np.sin(roll) * right + np.cos(roll) * down,
    )
    rotation = np.vstack([right, down, forward])
    translation = -rotation @ position
    width, height = frame_size
    k = np.array([[focal_px, 0.0, width / 2.0], [0.0, focal_px, height / 2.0], [0.0, 0.0, 1.0]])
    projection = k @ np.column_stack([rotation, translation])
    return TruthCamera(
        focal_px=focal_px,
        frame_size=frame_size,
        position_m=(float(position[0]), float(position[1]), float(position[2])),
        rotation=rotation,
        projection=np.asarray(projection, dtype=np.float64),
    )


def keypoint_array(keypoints: tuple[CourtKeypoint, ...] | list[CourtKeypoint]) -> Float64Array:
    return np.array([keypoint_xy(k) for k in keypoints], dtype=np.float64)
