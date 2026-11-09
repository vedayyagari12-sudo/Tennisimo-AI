"""Score the court engine on the TrackNet tennis dataset (real broadcast rallies
with per-frame ball labels; SESSION_MODE_PHASE2.md 11.2.3).

The dataset is NOT in the repo and must never be: download ``Dataset.zip``
from the TrackNet README's Google Drive folder, keep it outside the repo, and
pass its path. Frames are read from the zip. Each clip is written to a
temporary mp4 under ``--work`` (outside the repo), analysed, and the mp4 is
deleted at once. Nothing else is written except the JSONL of per-clip
numbers, which resumes when re-run with the same ``--out``.

Calibration is the optional court fit (step 19) on each clip's first frame,
falling back to the game's first fit. Labels: ``status`` 0 flying, 1 hit,
2 bounce; ``visibility`` 1 means clearly visible. A found event matches a
labelled one within +/- 2 frames (design 11.2).

    python -m scripts.tracknet_eval --zip PATH --work DIR --out FILE.jsonl --games 1,2,3,7,8,9,10
    python -m scripts.tracknet_eval --summary FILE.jsonl
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

from app.analysis.homography import apply_homography
from app.ball.court_lines import fit_court_from_frame
from app.models.court import BallEventKind, CourtCalibrationRequest, CourtFormat, CourtTapRequest
from app.services.court_offline import analyze_court_video

FPS = 30.0
MATCH_FRAMES = 2
GROSS_ERROR_M = 3.0


def match_count(truth: list[float], found: list[float], tolerance_s: float) -> int:
    """Greedy one-to-one matches of truth times to found times, nearest first."""
    used: set[int] = set()
    hits = 0
    for t in truth:
        best: int | None = None
        for i, f in enumerate(found):
            if i in used or abs(f - t) > tolerance_s:
                continue
            if best is None or abs(f - t) < abs(found[best] - t):
                best = i
        if best is not None:
            used.add(best)
            hits += 1
    return hits


def _frames(z: zipfile.ZipFile, game: int, clip: int) -> list[str]:
    prefix = f"Dataset/game{game}/Clip{clip}/"
    return sorted(n for n in z.namelist() if n.startswith(prefix) and n.endswith(".jpg"))


def _labels(z: zipfile.ZipFile, game: int, clip: int) -> list[dict[str, str]]:
    raw = z.read(f"Dataset/game{game}/Clip{clip}/Label.csv").decode()
    return list(csv.DictReader(io.StringIO(raw)))


def _image(z: zipfile.ZipFile, name: str) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(z.read(name), np.uint8), cv2.IMREAD_COLOR)


def fit_request(image: np.ndarray) -> CourtCalibrationRequest | None:
    height, width = image.shape[:2]
    fits = fit_court_from_frame(image)
    if not fits:
        return None
    taps = [
        CourtTapRequest(
            keypoint=t.keypoint,
            x=min(max(t.u_px / width, 0.0), 1.0),
            y=min(max(t.v_px / height, 0.0), 1.0),
        )
        for t in fits[0].taps
    ]
    return CourtCalibrationRequest(
        frame_time_s=0.0, frame_width_px=width, frame_height_px=height,
        court_format=CourtFormat.SINGLES, taps=taps,
    )


def run_clip(
    z: zipfile.ZipFile, game: int, clip: int, fallback: CourtCalibrationRequest | None, work: Path
) -> dict[str, object]:
    names = _frames(z, game, clip)
    labels = _labels(z, game, clip)
    first = _image(z, names[0])
    request = fit_request(first)
    source = "clip"
    if request is None:
        request, source = fallback, "game"
    truth_b = [i / FPS for i, r in enumerate(labels) if r["status"] == "2"]
    truth_h = [i / FPS for i, r in enumerate(labels) if r["status"] == "1"]
    row: dict[str, object] = {
        "game": game, "clip": clip, "frames": len(names), "fit": source if request else None,
        "truth_bounces": len(truth_b), "truth_hits": len(truth_h),
    }
    if request is None:
        return row
    work.mkdir(parents=True, exist_ok=True)
    video = work / f"g{game}c{clip}.mp4"
    height, width = first.shape[:2]
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (width, height))
    for name in names:
        writer.write(_image(z, name))
    writer.release()
    try:
        inputs, result = analyze_court_video(video, request)
    finally:
        video.unlink(missing_ok=True)  # the video is deleted after processing
    origin = float(inputs.frame_times_s[0]) if inputs.frame_times_s.size else 0.0
    bounces = [landing.bounce.time_s - origin for landing in result.landings]
    hits = [e.time_s - origin for e in result.events if e.kind is BallEventKind.HIT]
    tolerance = MATCH_FRAMES / FPS + 1e-6
    calibration = inputs.calibration_verdict.calibration if inputs.calibration_verdict else None
    errors: list[float] = []
    if calibration is not None and inputs.track_size:
        sx, sy = inputs.track_size[0] / width, inputs.track_size[1] / height
        for i, r in enumerate(labels):
            if r["status"] != "2" or not r["x-coordinate"]:
                continue
            near = [l for l in result.landings if abs(l.bounce.time_s - origin - i / FPS) <= tolerance]
            if not near:
                continue
            # A labelled bounce pixel is on the ground: the homography maps it.
            uv = np.array([[float(r["x-coordinate"]) * sx, float(r["y-coordinate"]) * sy]])
            gx, gy = apply_homography(calibration.h_image_to_court, uv)[0]
            best = min(near, key=lambda l: abs(l.bounce.time_s - origin - i / FPS))
            errors.append(float(np.hypot(best.bounce.court_xy_m[0] - gx, best.bounce.court_xy_m[1] - gy)))
    row.update(
        camera=inputs.stability.verdict.value,
        status=result.status.value,
        found_bounces=len(bounces),
        found_hits=len(hits),
        matched_bounces=match_count(truth_b, bounces, tolerance),
        matched_hits=match_count(truth_h, hits, tolerance),
        pos_err_m=[round(e, 3) for e in errors],
    )
    return row


def summarize(rows: list[dict[str, object]]) -> str:
    done = [r for r in rows if r.get("status") not in (None, "refused")]

    def line(rs: list[dict[str, object]], label: str) -> str:
        def total(key: str) -> int:
            return sum(int(r[key]) for r in rs)  # type: ignore[call-overload]

        tb, fb, mb = total("truth_bounces"), total("found_bounces"), total("matched_bounces")
        th, fh, mh = total("truth_hits"), total("found_hits"), total("matched_hits")
        err = np.array([e for r in rs for e in r["pos_err_m"]])  # type: ignore[attr-defined]
        ok = err[err < GROSS_ERROR_M]
        median = f"{np.median(ok):.2f}" if ok.size else "n/a"
        return (
            f"{label}: clips {len(rs)} | bounces {mb}/{tb} recall {mb / max(tb, 1):.2f} "
            f"precision {mb / max(fb, 1):.2f} | hits recall {mh / max(th, 1):.2f} "
            f"precision {mh / max(fh, 1):.2f} | median error {median} m, "
            f"over {GROSS_ERROR_M:.0f} m: {int((err >= GROSS_ERROR_M).sum())}/{err.size}"
        )

    out = [f"clips {len(rows)}, analysed {len(done)}", line(done, "ALL")]
    for game in sorted({int(r["game"]) for r in done}):  # type: ignore[call-overload]
        out.append(line([r for r in done if r["game"] == game], f"game{game}"))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--games", default="1,2,3,7,8,9,10")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if args.summary:
        print(summarize([json.loads(l) for l in args.summary.read_text().splitlines()]))
        return
    done = set()
    if args.out.exists():
        done = {(r["game"], r["clip"]) for r in map(json.loads, args.out.read_text().splitlines())}
    z = zipfile.ZipFile(args.zip)
    for game in (int(g) for g in args.games.split(",")):
        clips = sorted(
            int(n.split("/")[2][4:]) for n in z.namelist()
            if n.startswith(f"Dataset/game{game}/Clip") and n.endswith("/Label.csv")
        )
        fallback = None
        for clip in clips:
            fallback = fit_request(_image(z, _frames(z, game, clip)[0]))
            if fallback is not None:
                break
        for clip in clips:
            if (game, clip) in done:
                continue
            try:
                row = run_clip(z, game, clip, fallback, args.work)
            except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the run
                row = {"game": game, "clip": clip, "error": f"{type(exc).__name__}: {exc}"[:200]}
            print(json.dumps(row), flush=True)
            with args.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
