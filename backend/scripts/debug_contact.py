"""Visual debug for Stage 9 contact detection against real footage.

NOT a test, and not part of the pure layer: this script decodes video and writes
a .jpg, so cv2/PyAV I/O is fine HERE. Nothing in ``app/analysis/`` is touched.

Runs the real chain: decode -> PoseExtractor/extract_keypoints ->
build_pose_sequence -> Stage 7 normalize -> Stage 8 handedness -> Stage 9
contact, then saves a 5-frame filmstrip (contact +/- 2) with the detected frame
marked.

    python scripts/debug_contact.py [--clip PATH] [--out PATH] [--max-frames N]
                                    [--hint right|left] [--start-frame N]

``--start-frame`` skips N decoded frames before the analysis window begins, so a
window can be located partway into a clip (an approximation of what Stage 5's
keyframe motion scan is meant to produce). The window's first frame PTS becomes
``analysis_window_start_s``, so ``contact_absolute_time_s`` resolves back to a
timestamp in the ORIGINAL clip. Filmstrip labels and the reported contact frame
are in original-clip coordinates; window-relative indices are printed too.

Output defaults to backend/build/debug_contact/, which the repo .gitignore
already excludes via the ``build/`` pattern.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analysis.contact import detect_contact_frame  # noqa: E402
from app.analysis.handedness import detect_handedness  # noqa: E402
from app.analysis.normalize import normalize_sequence  # noqa: E402
from app.models.enums import Handedness  # noqa: E402
from app.pose.extractor import (  # noqa: E402
    PoseExtractor,
    extract_keypoints,
    resolve_model_path,
    verify_model_asset,
)
from app.pose.sequence import build_pose_sequence, detection_rate  # noqa: E402

# The PyAV decode helper already exists in the integration test; reuse it rather
# than writing a third decoder.
from tests.integration.test_pose_extractor_real_clip import (  # noqa: E402
    decode_rgb_frames,
    probe_frame_size,
)

DEFAULT_CLIP = Path.home() / "Desktop" / "tennis_clips" / "tennis_forehand_34449247.mp4"
DEFAULT_OUT_DIR = BACKEND_ROOT / "build" / "debug_contact"
STRIP_RADIUS = 2
TILE_WIDTH_PX = 360


def build_filmstrip(
    frames_rgb: list[np.ndarray],
    indices: list[int],
    contact_index: int,
    *,
    label_offset: int = 0,
) -> np.ndarray:
    """Horizontal filmstrip, one labelled tile per frame, contact marked.

    ``indices`` are window-relative; ``label_offset`` shifts the printed labels
    into original-clip frame numbers.
    """
    tiles: list[np.ndarray] = []
    for index in indices:
        rgb = frames_rgb[index]
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        scale = TILE_WIDTH_PX / float(bgr.shape[1])
        tile = cv2.resize(
            bgr, (TILE_WIDTH_PX, max(1, round(bgr.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        is_contact = index == contact_index
        label = f"frame {label_offset + index}" + ("  <= CONTACT" if is_contact else "")
        colour = (0, 0, 255) if is_contact else (255, 255, 255)

        banner_height = 34
        banner = np.zeros((banner_height, tile.shape[1], 3), dtype=np.uint8)
        cv2.putText(
            banner, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2, cv2.LINE_AA
        )
        tile = np.vstack([banner, tile])
        border = 6 if is_contact else 2
        tile = cv2.copyMakeBorder(
            tile, border, border, border, border, cv2.BORDER_CONSTANT, value=colour
        )
        tiles.append(tile)

    height = max(tile.shape[0] for tile in tiles)
    padded = [
        cv2.copyMakeBorder(
            tile, 0, height - tile.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0)
        )
        for tile in tiles
    ]
    return np.hstack(padded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", type=Path, default=DEFAULT_CLIP)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-frames", type=int, default=240)
    parser.add_argument("--hint", choices=["right", "left"], default=None)
    parser.add_argument("--start-frame", type=int, default=0)
    args = parser.parse_args()

    clip: Path = args.clip
    if not clip.is_file():
        print(f"clip not found: {clip}")
        return 2

    model_path = resolve_model_path()
    verify_model_asset(model_path)
    width_px, height_px = probe_frame_size(clip)

    start_frame = max(0, int(args.start_frame))
    # decode_rgb_frames counts from frame 0, so decode start+max and drop the
    # lead-in; the window's first frame keeps its true original-clip PTS.
    frames = decode_rgb_frames(clip, max_frames=start_frame + args.max_frames)
    frames = itertools.islice(frames, start_frame, None)
    try:
        head = next(frames)
    except StopIteration:
        print(f"clip has no frames at or after --start-frame {start_frame}")
        return 2
    window_start_s = head[0]

    kept: list[np.ndarray] = []

    def tee(source):
        for timestamp_s, frame_rgb in source:
            kept.append(frame_rgb)
            yield timestamp_s, frame_rgb

    with PoseExtractor(model_path) as extractor:
        raw = extract_keypoints(
            extractor,
            tee(itertools.chain([head], frames)),
            window_start_s=window_start_s,
            width_px=width_px,
            height_px=height_px,
            max_frames=args.max_frames,
        )

    seq = build_pose_sequence(raw, width_px, height_px)
    normalized, quality = normalize_sequence(seq)
    hint = Handedness(args.hint) if args.hint else None
    handedness = detect_handedness(normalized, hint)
    contact = detect_contact_frame(
        normalized, handedness, quality, analysis_window_start_s=window_start_s
    )

    print(f"clip                     : {clip}")
    print(f"frame size               : {width_px}x{height_px}")
    frame_count = seq.landmarks.shape[0]
    print(f"window start frame       : {start_frame}")
    print(
        "window frames (original) : "
        f"{start_frame}..{start_frame + frame_count - 1} ({frame_count} frames)"
    )
    print(f"frames processed         : {frame_count}")
    print(f"analysis_window_start_s  : {window_start_s:.4f}")
    print(f"detection rate           : {detection_rate(seq.detected):.4f}")
    print(
        "pose quality             : "
        f"usable={quality.usable} mean_vis={quality.mean_visibility:.3f} "
        f"longest_gap={quality.longest_gap_frames} "
        f"interpolated={quality.interpolated_frames} "
        f"torso_scale_px={quality.torso_scale_px:.2f} flags={quality.flags}"
    )
    print(
        "handedness               : "
        f"{handedness.handedness.value} confidence={handedness.confidence:.3f} "
        f"source={handedness.source.value} "
        f"racket_path_tu={handedness.racket_hand_path_length_tu:.3f} "
        f"off_hand_path_tu={handedness.off_hand_path_length_tu:.3f}"
    )
    if handedness.warnings:
        print(f"handedness warnings      : {handedness.warnings}")
    print(f"swing_direction_sign     : {normalized.swing_direction_sign:+d}")
    original_contact_index = start_frame + contact.frame_index
    print(f"contact frame_index (win): {contact.frame_index}")
    print(f"contact frame (original) : {original_contact_index}")
    print(f"contact time_s           : {contact.time_s:.4f}")
    print(f"contact_absolute_time_s  : {contact.contact_absolute_time_s:.4f}")
    expected_absolute_s = float(seq.timestamps_s[contact.frame_index])
    delta_s = abs(contact.contact_absolute_time_s - expected_absolute_s)
    print(
        "absolute-time check      : "
        f"expected={expected_absolute_s:.4f} "
        f"delta={delta_s:.6f} "
        f"{'OK' if delta_s <= 1e-3 else 'MISMATCH'}"
    )
    print(f"confidence               : {contact.confidence:.3f}")
    print(f"prominence_ratio         : {contact.prominence_ratio:.3f}")
    print(f"peak_frame_index         : {contact.peak_frame_index}")
    peak_hand_speed = contact.peak_hand_speed_tu_s
    print(
        "peak_hand_speed_tu_s     : "
        f"{'unavailable' if peak_hand_speed is None else format(peak_hand_speed, '.3f')}"
    )
    print(f"sanity gates fired       : {contact.sanity_flags or 'none'}")
    print(f"method                   : {contact.method}")

    last = len(kept) - 1
    indices = [
        index
        for index in range(
            contact.frame_index - STRIP_RADIUS, contact.frame_index + STRIP_RADIUS + 1
        )
        if 0 <= index <= last
    ]
    if not indices:
        print("no frames available to render")
        return 1

    strip = build_filmstrip(kept, indices, contact.frame_index, label_offset=start_frame)
    out_path: Path = args.out or (
        DEFAULT_OUT_DIR / f"{clip.stem}_contact_{original_contact_index}.jpg"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), strip, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    print(
        "filmstrip frames (orig)  : "
        f"{[start_frame + index for index in indices]}"
    )
    print(f"wrote                    : {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
