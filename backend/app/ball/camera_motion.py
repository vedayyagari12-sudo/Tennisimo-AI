"""Camera motion measurement for the fixed-camera check (design 2.4). PURE-CV.

Arrays in, numbers out: no file, no clock, no randomness. The verdict is the
pure ``analysis/camera_stability.py``; this module only measures.

Grayscale at ``ANALYSIS_LONG_EDGE_PX``, sampled at ``SAMPLE_INTERVAL_S`` (2 Hz).
``cv2.goodFeaturesToTrack`` runs ONCE, on the reference (first sampled) frame;
``cv2.calcOpticalFlowPyrLK`` then tracks those points into each sample. The
per-sample shift is the MEDIAN feature displacement, which ignores moving
players while more than half the features are static. Shifts are scaled to the
pixel space of the frames handed in (TRACK_SPACE in Court mode).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from app.pose.video_io import resize_to_long_edge

__all__ = [
    "ANALYSIS_LONG_EDGE_PX",
    "SAMPLE_INTERVAL_S",
    "CameraMotionSampler",
    "CameraMotionSamples",
    "median_feature_shift",
    "reference_features",
]

U8Array = npt.NDArray[np.uint8]
F32Array = npt.NDArray[np.float32]

ANALYSIS_LONG_EDGE_PX: Final[int] = 640
SAMPLE_INTERVAL_S: Final[float] = 0.5
#: goodFeaturesToTrack settings at 640 px. Starting values.
MAX_FEATURES: Final[int] = 300
FEATURE_QUALITY: Final[float] = 0.01
FEATURE_MIN_DISTANCE_PX: Final[float] = 8.0
FEATURE_BLOCK_SIZE: Final[int] = 7
#: Pyramidal Lucas-Kanade settings at 640 px.
LK_WINDOW_PX: Final[tuple[int, int]] = (21, 21)
LK_MAX_LEVEL: Final[int] = 3
LK_CRITERIA: Final[tuple[int, int, float]] = (
    cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
    30,
    0.01,
)


@dataclass(frozen=True, eq=False)
class CameraMotionSamples:
    """What ``assess_camera_stability`` consumes."""

    times_s: npt.NDArray[np.float64]
    #: (N, 2), relative to the first sample, in the input frames' pixels.
    shifts_px: npt.NDArray[np.float64]
    feature_counts: npt.NDArray[np.int32]
    #: Long edge of the input frames: the unit of ``shifts_px``.
    long_edge_px: int


def reference_features(reference_gray: U8Array) -> F32Array:
    """Corners to track, (N, 1, 2) float32; (0, 1, 2) on a featureless frame."""
    points = cv2.goodFeaturesToTrack(
        reference_gray,
        maxCorners=MAX_FEATURES,
        qualityLevel=FEATURE_QUALITY,
        minDistance=FEATURE_MIN_DISTANCE_PX,
        blockSize=FEATURE_BLOCK_SIZE,
    )
    if points is None:
        return np.zeros((0, 1, 2), dtype=np.float32)
    return np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)


def median_feature_shift(
    reference_gray: U8Array, frame_gray: U8Array, points: F32Array
) -> tuple[float, float, int]:
    """(median dx, median dy, features tracked) from reference to frame.

    NaN shifts and a count of 0 when nothing could be tracked.
    """
    if len(points) == 0:
        return math.nan, math.nan, 0
    moved, status, _ = cv2.calcOpticalFlowPyrLK(
        reference_gray,
        frame_gray,
        points,
        None,
        winSize=LK_WINDOW_PX,
        maxLevel=LK_MAX_LEVEL,
        criteria=LK_CRITERIA,
    )
    if moved is None or status is None:
        return math.nan, math.nan, 0
    delta = (moved - points).reshape(-1, 2).astype(np.float64)
    ok = (status.reshape(-1) == 1) & np.all(np.isfinite(delta), axis=1)
    if not np.any(ok):
        return math.nan, math.nan, 0
    return (
        float(np.median(delta[ok, 0])),
        float(np.median(delta[ok, 1])),
        int(np.count_nonzero(ok)),
    )


class CameraMotionSampler:
    """Feed every frame of a stream in order; it measures at 2 Hz.

    The first frame added is the reference. Thereafter a frame is measured when
    its time reaches the next sample target (reference time + k x interval),
    so real PTS decide which frame is sampled and nothing is resampled.
    """

    def __init__(
        self,
        *,
        sample_interval_s: float = SAMPLE_INTERVAL_S,
        analysis_long_edge_px: int = ANALYSIS_LONG_EDGE_PX,
    ) -> None:
        if sample_interval_s <= 0.0 or analysis_long_edge_px <= 0:
            raise ValueError("sample interval and analysis size must be positive")
        self._interval_s = sample_interval_s
        self._analysis_long_edge_px = analysis_long_edge_px
        self._reference: U8Array | None = None
        self._points: F32Array = np.zeros((0, 1, 2), dtype=np.float32)
        self._start_s = 0.0
        self._next_index = 0
        self._scale = 1.0
        self._long_edge_px = 0
        self._times: list[float] = []
        self._shifts: list[tuple[float, float]] = []
        self._counts: list[int] = []

    def _small_gray(self, frame_bgr: U8Array) -> U8Array:
        small = resize_to_long_edge(frame_bgr, self._analysis_long_edge_px)
        return np.asarray(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), dtype=np.uint8)

    def add(self, time_s: float, frame_bgr: U8Array) -> None:
        if self._reference is None:
            self._reference = self._small_gray(frame_bgr)
            self._points = reference_features(self._reference)
            self._start_s = time_s
            self._long_edge_px = int(max(frame_bgr.shape[:2]))
            self._scale = self._long_edge_px / float(max(self._reference.shape[:2]))
            self._record(time_s, 0.0, 0.0, len(self._points))
            return
        if time_s < self._start_s + self._next_index * self._interval_s:
            return
        dx, dy, count = median_feature_shift(self._reference, self._small_gray(frame_bgr), self._points)
        self._record(time_s, dx * self._scale, dy * self._scale, count)

    def _record(self, time_s: float, dx: float, dy: float, count: int) -> None:
        self._times.append(float(time_s))
        self._shifts.append((dx, dy))
        self._counts.append(int(count))
        elapsed = time_s - self._start_s
        self._next_index = int(math.floor(elapsed / self._interval_s + 1e-9)) + 1

    def result(self) -> CameraMotionSamples:
        return CameraMotionSamples(
            times_s=np.asarray(self._times, dtype=np.float64),
            shifts_px=np.asarray(self._shifts, dtype=np.float64).reshape(-1, 2),
            feature_counts=np.asarray(self._counts, dtype=np.int32),
            long_edge_px=self._long_edge_px,
        )
