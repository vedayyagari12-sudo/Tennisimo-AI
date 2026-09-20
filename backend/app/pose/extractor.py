"""MediaPipe Tasks PoseLandmarker boundary (PIPELINE.md Stage 6).

IMPURE: ML model inference plus filesystem access. This is the ONLY module in
the repository permitted to import ``mediapipe`` (PIPELINE.md 4.4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Final

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    PoseLandmarker,
    PoseLandmarkerOptions,
    RunningMode,
)

from app.models.internal import RawPoseSequence
from app.pose.sequence import frame_timestamps_ms

POSE_MODEL_FILENAME: Final[str] = "pose_landmarker_full.task"
# SHA-256 of the vendored bundle (PIPELINE.md Stage 6.1). Regenerate and commit
# in the same commit as any model change.
POSE_MODEL_SHA256: Final[str] = (
    "4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad"
)
POSE_MODEL_SIZE_BYTES: Final[int] = 9_398_198
NUM_LANDMARKS: Final[int] = 33

_DIGEST_CHUNK_BYTES: Final[int] = 1 << 20


def resolve_model_path(configured: Path | None = None) -> Path:
    """Return the configured bundle path if given, else the vendored one."""
    if configured is not None:
        return Path(configured)
    return Path(__file__).resolve().parent / "models" / POSE_MODEL_FILENAME


def verify_model_asset(path: Path) -> None:
    """Verify the bundle's size and SHA-256 against the pinned constants.

    Called ONCE at application startup, never per job. A truncated or
    LFS-pointer-substituted checkout does not make MediaPipe fail loudly -- it
    can yield a landmarker that loads and emits plausible but wrong landmarks.
    This turns that class of failure into a refusal to boot.

    Raises:
        RuntimeError: on a missing file, a size mismatch, or a digest mismatch.
    """
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"pose model asset missing: {path}")

    size = path.stat().st_size
    if size != POSE_MODEL_SIZE_BYTES:
        raise RuntimeError(
            f"pose model asset size mismatch for {path}: "
            f"expected {POSE_MODEL_SIZE_BYTES} bytes, found {size}"
        )

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_DIGEST_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != POSE_MODEL_SHA256:
        raise RuntimeError(
            f"pose model asset digest mismatch for {path}: "
            f"expected {POSE_MODEL_SHA256}, found {actual}"
        )


class PoseExtractor:
    """Owns one ``PoseLandmarker`` for the lifetime of one job (Stage 6.7).

    This wrapper -- not MediaPipe's object -- owns the context-manager
    protocol: ``__exit__`` calls ``PoseLandmarker.close()`` unconditionally so
    the graph and the native TFLite arena are released before Stage 10 opens
    its own decode pass.
    """

    def __init__(
        self,
        model_path: Path,
        *,
        num_poses: int = 1,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self.model_path: Path = Path(model_path)
        options = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(self.model_path)),
            running_mode=RunningMode.VIDEO,
            num_poses=num_poses,
            min_pose_detection_confidence=min_pose_detection_confidence,
            min_pose_presence_confidence=min_pose_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_segmentation_masks=False,
            result_callback=None,
        )
        self._landmarker: PoseLandmarker | None = PoseLandmarker.create_from_options(
            options
        )

    def __enter__(self) -> "PoseExtractor":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Tear down the graph and release the native arena. Idempotent."""
        landmarker = self._landmarker
        self._landmarker = None
        if landmarker is not None:
            landmarker.close()

    def detect_frame(
        self, frame_rgb: np.ndarray, timestamp_ms: int
    ) -> tuple[np.ndarray, np.ndarray, bool]:
        """Run VIDEO-mode inference on one RGB frame.

        Returns ``(landmarks (33,4) float32, world (33,3) float32, detected)``.
        On non-detection returns zero-filled arrays and ``False``.
        """
        if self._landmarker is None:
            raise RuntimeError("PoseExtractor is closed")

        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(frame_rgb, dtype=np.uint8),
        )
        result = self._landmarker.detect_for_video(image, int(timestamp_ms))

        landmarks = np.zeros((NUM_LANDMARKS, 4), dtype=np.float32)
        world = np.zeros((NUM_LANDMARKS, 3), dtype=np.float32)

        # Non-detection is an EMPTY OUTER LIST, not None, and a frame is only
        # usable if BOTH lists are populated (Stage 6.5).
        detected = (
            len(result.pose_landmarks) > 0 and len(result.pose_world_landmarks) > 0
        )
        if not detected:
            return landmarks, world, False

        person = result.pose_landmarks[0]
        world_person = result.pose_world_landmarks[0]
        for i in range(NUM_LANDMARKS):
            lm = person[i]
            # Channel 4 is `visibility`; `presence` is deliberately ignored.
            landmarks[i, 0] = lm.x
            landmarks[i, 1] = lm.y
            landmarks[i, 2] = lm.z
            landmarks[i, 3] = lm.visibility
            wlm = world_person[i]
            world[i, 0] = wlm.x
            world[i, 1] = wlm.y
            world[i, 2] = wlm.z
        return landmarks, world, True


def extract_keypoints(
    extractor: PoseExtractor,
    frames: Iterable[tuple[float, np.ndarray]],
    *,
    window_start_s: float,
    width_px: int,
    height_px: int,
    max_frames: int = 240,
) -> RawPoseSequence:
    """Drive the Stage 5 generator exactly once, streaming, into a RawPoseSequence.

    Accumulates into preallocated ``(max_frames, ...)`` buffers, trimmed on
    exit. Does NOT construct or close the extractor. Does NOT raise
    ``NO_POSE_DETECTED`` -- it reports ``detected`` and the orchestrator applies
    the 40 % gate.

    ``width_px``/``height_px`` are part of Stage 6's contract because they
    travel with the frames; they are carried onto the seam by
    ``build_pose_sequence``, not by ``RawPoseSequence``.
    """
    if max_frames <= 0:
        raise ValueError("max_frames must be positive")
    if width_px <= 0 or height_px <= 0:
        raise ValueError("width_px and height_px must be positive")

    landmarks_buf = np.zeros((max_frames, NUM_LANDMARKS, 4), dtype=np.float32)
    world_buf = np.zeros((max_frames, NUM_LANDMARKS, 3), dtype=np.float32)
    timestamps_buf = np.zeros((max_frames,), dtype=np.float64)
    detected_buf = np.zeros((max_frames,), dtype=bool)

    count = 0
    previous_ms = -1
    for timestamp_s, frame_rgb in frames:
        if count >= max_frames:
            break
        # The rounding + monotonic clamp is the PURE rule from sequence.py,
        # applied one frame at a time because the generator is streaming.
        timestamp_ms = int(
            frame_timestamps_ms(
                np.asarray([timestamp_s], dtype=np.float64),
                window_start_s,
                previous_ms=previous_ms,
            )[0]
        )
        previous_ms = timestamp_ms

        lm, world, detected = extractor.detect_frame(frame_rgb, timestamp_ms)
        landmarks_buf[count] = lm
        world_buf[count] = world
        # Full-precision PTS is stored; the millisecond value is discarded.
        timestamps_buf[count] = timestamp_s
        detected_buf[count] = detected
        count += 1

    return RawPoseSequence(
        landmarks=landmarks_buf[:count],
        world=world_buf[:count],
        timestamps_s=timestamps_buf[:count],
        detected=detected_buf[:count],
    )
