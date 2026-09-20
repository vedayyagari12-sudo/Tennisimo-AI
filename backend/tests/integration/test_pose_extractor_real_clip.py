"""Integration test: the real PoseExtractor against a real clip.

Runs actual MediaPipe inference, so it is skipped unless the clip directory is
present (override with TENNIS_CLIPS_DIR). The decode helper below is NOT Stage 5
-- Stage 5 (`app/pose/video_io.py`) does not exist yet. This is the minimum
PyAV decode needed to drive the extractor: no rotation handling, no 30 fps
resampling, no keyframe motion scan.

Run with `-s` to see the report printed by `test_real_clip_extraction`.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import av
import cv2
import numpy as np
import pytest

from app.pose.extractor import PoseExtractor, extract_keypoints, resolve_model_path, verify_model_asset
from app.pose.sequence import build_pose_sequence, detection_rate, validate_sequence_invariants

CLIP_DIR = Path(
    os.environ.get("TENNIS_CLIPS_DIR", str(Path.home() / "Desktop" / "tennis_clips"))
)
CLIP_NAME = "tennis_forehand_10340703.mp4"
LONG_EDGE_PX = 640

pytestmark = pytest.mark.skipif(
    not (CLIP_DIR / CLIP_NAME).is_file(), reason=f"real clip not available in {CLIP_DIR}"
)


def decode_rgb_frames(
    path: Path, *, long_edge_px: int = LONG_EDGE_PX, max_frames: int = 240
) -> Iterator[tuple[float, np.ndarray]]:
    """Minimal PyAV decode: yields (pts_seconds, rgb frame) downscaled. Not Stage 5."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        time_base = float(stream.time_base)
        emitted = 0
        for frame in container.decode(stream):
            if emitted >= max_frames:
                return
            timestamp_s = (
                float(frame.pts) * time_base if frame.pts is not None else float(emitted) / 30.0
            )
            rgb = frame.to_ndarray(format="rgb24")
            height, width = rgb.shape[:2]
            scale = long_edge_px / float(max(height, width))
            if scale < 1.0:
                rgb = cv2.resize(
                    rgb,
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            emitted += 1
            yield timestamp_s, np.ascontiguousarray(rgb)


def probe_frame_size(path: Path) -> tuple[int, int]:
    """(width, height) of the first decoded+downscaled frame."""
    for _, frame in decode_rgb_frames(path, max_frames=1):
        return int(frame.shape[1]), int(frame.shape[0])
    raise AssertionError("clip produced no frames")


def test_real_clip_extraction() -> None:
    clip = CLIP_DIR / CLIP_NAME
    model_path = resolve_model_path()
    verify_model_asset(model_path)
    width_px, height_px = probe_frame_size(clip)

    # The window starts at the first frame's real PTS, so peek one frame and
    # push it back with itertools.chain rather than decoding twice.
    frames = decode_rgb_frames(clip)
    head = next(frames)
    window_start_s = head[0]

    with PoseExtractor(model_path) as extractor:
        raw = extract_keypoints(
            extractor,
            itertools.chain([head], frames),
            window_start_s=window_start_s,
            width_px=width_px,
            height_px=height_px,
        )

    seq = build_pose_sequence(raw, width_px, height_px)
    validate_sequence_invariants(seq)
    rate = detection_rate(seq.detected)

    print(f"\nclip                : {clip}")
    print(f"frame size          : {width_px}x{height_px}")
    print(f"frames processed    : {seq.landmarks.shape[0]}")
    print(f"detection rate      : {rate:.4f}")
    print(f"landmarks           : {seq.landmarks.shape} {seq.landmarks.dtype}")
    print(f"world               : {seq.world.shape} {seq.world.dtype}")
    print(f"timestamps_s        : {seq.timestamps_s.shape} {seq.timestamps_s.dtype}")
    print(f"detected            : {seq.detected.shape} {seq.detected.dtype}")
    print(f"timestamps_s[:5]    : {seq.timestamps_s[:5]}")

    index = int(np.argmax(seq.detected))
    named = {
        "right_shoulder(12)": 12,
        "right_elbow(14)": 14,
        "right_wrist(16)": 16,
        "left_wrist(15)": 15,
        "right_hip(24)": 24,
        "left_hip(23)": 23,
    }
    print(f"first detected frame: {index} (t={seq.timestamps_s[index]:.4f}s)")
    for label, landmark_index in named.items():
        x, y, z, visibility = seq.landmarks[index, landmark_index]
        wx, wy, wz = seq.world[index, landmark_index]
        print(
            f"  {label:<19} x={x:+.4f} y={y:+.4f} z={z:+.4f} vis={visibility:.4f}"
            f" | world m=({wx:+.4f}, {wy:+.4f}, {wz:+.4f})"
        )

    print(
        "detection map       : "
        + "".join("X" if flag else "." for flag in seq.detected)
    )

    assert seq.landmarks.shape[0] > 0
    # NOT a 40 % gate: Stage 6 deliberately does not raise NO_POSE_DETECTED --
    # it reports `detected` and the orchestrator applies the gate. This stock
    # clip has no player in frame for most of its first 240 frames (measured
    # rate 0.179), so the assertion here is only that real detections happened.
    assert int(np.count_nonzero(seq.detected)) >= 20
    # Normalized landmarks must be in frame, and visibility a probability.
    detected_landmarks = seq.landmarks[seq.detected]
    assert np.all(detected_landmarks[:, :, 0] > -0.5)
    assert np.all(detected_landmarks[:, :, 0] < 1.5)
    assert np.all(detected_landmarks[:, :, 3] >= 0.0)
    assert np.all(detected_landmarks[:, :, 3] <= 1.0)
    # Timestamps are the real PTS: strictly increasing floats, not whole ms.
    assert np.all(np.diff(seq.timestamps_s) > 0)


def test_extractor_close_is_idempotent_and_blocks_further_use() -> None:
    extractor = PoseExtractor(resolve_model_path())
    extractor.close()
    extractor.close()
    with pytest.raises(RuntimeError, match="closed"):
        extractor.detect_frame(np.zeros((64, 64, 3), dtype=np.uint8), 0)


def test_extractor_exit_closes_on_exception() -> None:
    extractor = PoseExtractor(resolve_model_path())
    with pytest.raises(ValueError):
        with extractor:
            raise ValueError("boom")
    with pytest.raises(RuntimeError, match="closed"):
        extractor.detect_frame(np.zeros((64, 64, 3), dtype=np.uint8), 0)


def test_blank_frames_report_non_detection() -> None:
    blank = [(i / 30.0, np.zeros((360, 640, 3), dtype=np.uint8)) for i in range(4)]
    with PoseExtractor(resolve_model_path()) as extractor:
        raw = extract_keypoints(
            extractor, blank, window_start_s=0.0, width_px=640, height_px=360
        )
    assert raw.detected.shape == (4,)
    assert detection_rate(raw.detected) == 0.0
    assert np.all(raw.landmarks == 0.0)
    assert np.all(raw.world == 0.0)
