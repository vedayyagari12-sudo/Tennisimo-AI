"""Stage 9 diagnostic: dump the FULL racket-hand speed curve for a real clip.

NOT a test, and not part of the pure layer: this script decodes video and writes
images/CSV, so cv2/PyAV I/O is fine HERE. Nothing in ``app/analysis/`` is
touched or modified -- this is read-only diagnosis.

Runs the same real chain as ``debug_contact.py`` (decode -> PoseExtractor/
extract_keypoints -> build_pose_sequence -> Stage 7 normalize -> Stage 8
handedness -> Stage 9 contact), then:

  * prints the frame-by-frame racket-hand speed s(t) in TU/s, marking
    ``peak_index`` and the detected ``contact_index`` (deceleration onset),
  * writes the same table as CSV,
  * writes a WIDE filmstrip grid spanning ``peak_index - PAD .. contact_index +
    PAD`` (clamped), each tile labelled with frame number, speed, and PEAK /
    CONTACT markers, so the true racket-ball impact frame can be identified by
    eye and compared against the algorithm.

    python scripts/dump_contact_curve.py --clip PATH [--hint right|left]
                                         [--max-frames N] [--start-frame N]
                                         [--pad N] [--out-dir PATH]

Output defaults to backend/build/debug_contact/, which the repo .gitignore
already excludes via the ``build/`` pattern.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analysis.contact import (  # noqa: E402
    forward_swing_window_start,
    racket_hand_speed,
    racket_wrist_reliable,
    detect_contact_frame,
)
from app.analysis.handedness import detect_handedness, racket_wrist_index  # noqa: E402
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

DEFAULT_OUT_DIR = BACKEND_ROOT / "build" / "debug_contact"
DEFAULT_PAD_FRAMES = 8
TILE_WIDTH_PX = 320
GRID_COLUMNS = 6
BANNER_HEIGHT_PX = 52


def build_filmstrip_grid(
    frames_rgb: list[np.ndarray],
    indices: list[int],
    speed: np.ndarray,
    peak_index: int,
    contact_index: int,
    *,
    label_offset: int = 0,
    columns: int = GRID_COLUMNS,
) -> np.ndarray:
    """Grid of labelled tiles: frame number, speed, PEAK / CONTACT markers.

    ``indices`` are window-relative; ``label_offset`` shifts the printed labels
    into original-clip frame numbers.
    """
    tiles: list[np.ndarray] = []
    for index in indices:
        rgb = frames_rgb[index]
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        scale = TILE_WIDTH_PX / float(bgr.shape[1])
        tile = cv2.resize(
            bgr,
            (TILE_WIDTH_PX, max(1, round(bgr.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        is_peak = index == peak_index
        is_contact = index == contact_index
        marker = ""
        if is_peak and is_contact:
            marker = "  PEAK+CONTACT"
        elif is_peak:
            marker = "  << PEAK"
        elif is_contact:
            marker = "  << CONTACT"
        if is_contact:
            colour = (0, 0, 255)
        elif is_peak:
            colour = (0, 215, 255)
        else:
            colour = (255, 255, 255)

        value = float(speed[index]) if 0 <= index < speed.shape[0] else float("nan")
        banner = np.zeros((BANNER_HEIGHT_PX, tile.shape[1], 3), dtype=np.uint8)
        cv2.putText(
            banner,
            f"f{label_offset + index}{marker}",
            (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            colour,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            banner,
            f"s={value:.2f} TU/s",
            (8, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            colour,
            1,
            cv2.LINE_AA,
        )
        tile = np.vstack([banner, tile])
        border = 6 if (is_contact or is_peak) else 2
        tile = cv2.copyMakeBorder(
            tile, border, border, border, border, cv2.BORDER_CONSTANT, value=colour
        )
        tiles.append(tile)

    tile_height = max(tile.shape[0] for tile in tiles)
    tile_width = max(tile.shape[1] for tile in tiles)
    padded = [
        cv2.copyMakeBorder(
            tile,
            0,
            tile_height - tile.shape[0],
            0,
            tile_width - tile.shape[1],
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        for tile in tiles
    ]

    rows: list[np.ndarray] = []
    for start in range(0, len(padded), columns):
        chunk = padded[start : start + columns]
        while len(chunk) < columns:
            chunk.append(np.zeros_like(padded[0]))
        rows.append(np.hstack(chunk))
    return np.vstack(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-frames", type=int, default=240)
    parser.add_argument("--hint", choices=["right", "left"], default=None)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--pad", type=int, default=DEFAULT_PAD_FRAMES)
    args = parser.parse_args()

    clip: Path = args.clip
    if not clip.is_file():
        print(f"clip not found: {clip}")
        return 2

    model_path = resolve_model_path()
    verify_model_asset(model_path)
    width_px, height_px = probe_frame_size(clip)

    start_frame = max(0, int(args.start_frame))
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

    speed = racket_hand_speed(normalized, handedness.handedness)
    reliable = racket_wrist_reliable(normalized, handedness.handedness)
    points = np.asarray(normalized.points, dtype=np.float64)
    wrist = racket_wrist_index(handedness.handedness)
    forward = points[:, wrist, 0] * float(normalized.swing_direction_sign)
    window_start = forward_swing_window_start(forward, contact.peak_frame_index)
    timestamps_s = np.asarray(seq.timestamps_s, dtype=np.float64)
    visibility = np.asarray(normalized.visibility, dtype=np.float64)

    frame_count = int(seq.landmarks.shape[0])
    peak_index = int(contact.peak_frame_index)
    contact_index = int(contact.frame_index)

    print(f"clip                     : {clip}")
    print(f"frame size               : {width_px}x{height_px}")
    print(f"window start frame       : {start_frame}")
    print(
        "window frames (original) : "
        f"{start_frame}..{start_frame + frame_count - 1} ({frame_count} frames)"
    )
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
    print(f"forward_swing_window_start: {window_start} (original {start_frame + window_start})")
    print(f"peak_frame_index (win)   : {peak_index} (original {start_frame + peak_index})")
    peak_speed = contact.peak_hand_speed_tu_s
    print(
        "peak_hand_speed_tu_s     : "
        f"{'unavailable' if peak_speed is None else format(peak_speed, '.4f')}"
    )
    print(
        f"contact frame_index (win): {contact_index} (original {start_frame + contact_index})"
    )
    print(
        "contact speed_tu_s       : "
        f"{float(speed[contact_index]) if contact_index < speed.shape[0] else float('nan'):.4f}"
    )
    print(f"contact - peak           : {contact_index - peak_index} frames")
    print(f"contact time_s           : {contact.time_s:.4f}")
    print(f"contact_absolute_time_s  : {contact.contact_absolute_time_s:.4f}")
    print(f"confidence               : {contact.confidence:.3f}")
    print(f"prominence_ratio         : {contact.prominence_ratio:.3f}")
    print(f"sanity gates fired       : {contact.sanity_flags or 'none'}")
    print(f"method                   : {contact.method}")

    print()
    if peak_speed is None:
        # Stage 9 reported no measurable peak speed, so the 5 % decel-onset
        # threshold it would anchor is undefined. Say so instead of printing a
        # number derived from a missing measurement.
        print("5% drop threshold        : unavailable (no peak_hand_speed_tu_s)")
    else:
        print(f"5% drop threshold        : {peak_speed * 0.95:.4f} TU/s")
    print()
    header = (
        f"{'orig':>5} {'win':>4} {'t_s':>8} {'speed_tu_s':>11} "
        f"{'fwd':>8} {'vis':>5} {'rel':>4}  mark"
    )
    print(header)
    print("-" * len(header))

    rows: list[dict[str, object]] = []
    for index in range(frame_count):
        marks: list[str] = []
        if index == peak_index:
            marks.append("PEAK")
        if index == contact_index:
            marks.append("CONTACT(decel-onset)")
        if index == window_start:
            marks.append("fwd-window-start")
        mark = " ".join(marks)
        value = float(speed[index]) if index < speed.shape[0] else float("nan")
        vis = (
            float(visibility[index, wrist])
            if visibility.ndim == 2 and index < visibility.shape[0]
            else float("nan")
        )
        rel = bool(reliable[index]) if index < reliable.shape[0] else False
        print(
            f"{start_frame + index:>5} {index:>4} {float(timestamps_s[index]):>8.4f} "
            f"{value:>11.4f} {float(forward[index]):>8.3f} {vis:>5.2f} "
            f"{'Y' if rel else 'n':>4}  {mark}"
        )
        rows.append(
            {
                "original_frame": start_frame + index,
                "window_frame": index,
                "time_s": round(float(timestamps_s[index]), 6),
                "speed_tu_s": round(value, 6),
                "forward_displacement_tu": round(float(forward[index]), 6),
                "wrist_visibility": round(vis, 4),
                "velocity_reliable": int(rel),
                "mark": mark,
            }
        )

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{clip.stem}_speed_curve.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print()
    print(f"wrote csv                : {csv_path.resolve()}")

    last = len(kept) - 1
    pad = max(0, int(args.pad))
    first_index = max(0, min(peak_index, contact_index) - pad)
    last_index = min(last, max(peak_index, contact_index) + pad)
    indices = list(range(first_index, last_index + 1))
    if not indices:
        print("no frames available to render")
        return 1

    strip = build_filmstrip_grid(
        kept,
        indices,
        speed,
        peak_index,
        contact_index,
        label_offset=start_frame,
    )
    strip_path = out_dir / f"{clip.stem}_wide_strip_{start_frame + first_index}_{start_frame + last_index}.jpg"
    cv2.imwrite(str(strip_path), strip, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    print(
        "filmstrip frames (orig)  : "
        f"{start_frame + first_index}..{start_frame + last_index}"
    )
    print(f"wrote filmstrip          : {strip_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
