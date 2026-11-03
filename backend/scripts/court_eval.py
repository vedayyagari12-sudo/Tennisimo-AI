"""Court mode evaluation, minimal: ``--mode candidates`` (brief batch E).

NOT a test and not part of the pure layer: it decodes video and writes files.
It runs the step 7-8 front end over one clip and reports what the design 1.4
experiment needs before any event logic exists:

* per-frame ball candidates (``flight_detector``) over the TRACK_SPACE stream
  (``flight_frames``), with the court ROI and expected ball size when taps are
  given,
* the fixed-camera verdict (``camera_motion`` + ``camera_stability``),
* with hand labels, the share of visible ball labels that have a candidate
  within 3 px (design 1.4, first row).

    python scripts/court_eval.py --mode candidates --video PATH
        [--taps-json PATH] [--labels-jsonl PATH] [--out NAME]

``--taps-json`` is a ``CourtCalibrationRequest`` body (taps as 0-1 fractions of
the video frame). ``--labels-jsonl`` uses the design 11.4 label format; only
``ball`` lines (and an optional ``session`` line giving the labelled frame's
width and height) are read. Label pixels are native video pixels and label
``frame`` is the 0-based decode-order index; the 3 px match is measured in
label pixels.

Output: ``backend/build/court_eval/<NAME>/`` (default NAME: the video stem),
which the repo ``.gitignore`` excludes via ``build/``. ``candidates.npy`` holds
the ``FLIGHT_CANDIDATE_DTYPE`` records, ``frame_times.npy`` the PTS of every
decoded frame, ``summary.json`` the numbers, also printed. The video path is
an argument; nothing from the clip is ever written outside build/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Final, Literal

import cv2
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analysis.camera_stability import assess_camera_stability  # noqa: E402
from app.analysis.court_calibration import CourtCalibration, CourtTap, calibrate_court  # noqa: E402
from app.ball.camera_motion import CameraMotionSampler  # noqa: E402
from app.ball.flight_detector import (  # noqa: E402
    FLIGHT_CANDIDATE_DTYPE,
    court_roi_mask,
    expected_diameter_fn,
    find_flight_candidates,
)
from app.ball.flight_frames import iter_track_frames, probe_court_clip  # noqa: E402
from app.models.court import CourtCalibrationRequest  # noqa: E402

OUTPUT_ROOT: Final[Path] = BACKEND_ROOT / "build" / "court_eval"
#: Design 1.4: a candidate within 3 px of the label counts.
LABEL_MATCH_PX: Final[float] = 3.0
_SAFE_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BallLabel(BaseModel):
    """One ``ball`` line of the design 11.4 label format."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: Literal["ball"]
    frame: int
    u_px: float
    v_px: float
    visible: bool


class SessionLabel(BaseModel):
    """The ``session`` line; only the labelled frame size is used here."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: Literal["session"]
    width: int
    height: int


def output_dir(name: str) -> Path:
    """``build/court_eval/<name>``. A plain name only: never a path that could
    write into the repo.

    Raises:
        ValueError: if ``name`` is not a plain file name.
    """
    if not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise ValueError(f"--out must be a plain name, got {name!r}")
    return OUTPUT_ROOT / name


def parse_labels(lines: Iterable[str]) -> tuple[list[BallLabel], SessionLabel | None]:
    balls: list[BallLabel] = []
    session: SessionLabel | None = None
    for line in lines:
        text = line.strip()
        if not text:
            continue
        kind = json.loads(text).get("type")
        if kind == "ball":
            balls.append(BallLabel.model_validate_json(text))
        elif kind == "session":
            session = SessionLabel.model_validate_json(text)
    return balls, session


def label_match(
    candidates: npt.NDArray[np.void],
    labels: Sequence[BallLabel],
    label_to_track: float,
    evaluated_frames: set[int],
    *,
    tolerance_px: float = LABEL_MATCH_PX,
) -> tuple[int, int]:
    """(visible labels on evaluated frames, those with a candidate within
    ``tolerance_px``). Distances are in LABEL pixels: candidates are divided
    by ``label_to_track`` (track px per label px)."""
    by_frame: dict[int, npt.NDArray[np.float64]] = {}
    for frame in np.unique(candidates["frame_index"]):
        rows = candidates[candidates["frame_index"] == frame]
        by_frame[int(frame)] = np.column_stack([rows["x"], rows["y"]]) / label_to_track
    total = 0
    matched = 0
    for label in labels:
        if not label.visible or label.frame not in evaluated_frames:
            continue
        total += 1
        found = by_frame.get(label.frame)
        if found is not None and float(
            np.min(np.hypot(found[:, 0] - label.u_px, found[:, 1] - label.v_px))
        ) <= tolerance_px:
            matched += 1
    return total, matched


def count_summary(per_frame: npt.NDArray[np.int64]) -> dict[str, float | int]:
    """Candidates per evaluated frame: total, mean, median, p95, max."""
    if per_frame.size == 0:
        return {"frames_evaluated": 0, "total": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0}
    return {
        "frames_evaluated": int(per_frame.size),
        "total": int(per_frame.sum()),
        "mean": round(float(per_frame.mean()), 3),
        "median": float(np.median(per_frame)),
        "p95": float(np.percentile(per_frame, 95.0)),
        "max": int(per_frame.max()),
    }


def taps_in_track_space(request: CourtCalibrationRequest, track_size: tuple[int, int]) -> list[CourtTap]:
    """Normalised taps to TRACK_SPACE pixels (brief Constants)."""
    width, height = track_size
    return [CourtTap(t.keypoint, t.x * width, t.y * height) for t in request.taps]


def run_candidates(
    video: Path, taps_json: Path | None, labels_jsonl: Path | None
) -> tuple[dict[str, Any], npt.NDArray[np.void], npt.NDArray[np.float64]]:
    started = time.monotonic()
    probe = probe_court_clip(video)
    request = (
        CourtCalibrationRequest.model_validate_json(taps_json.read_text(encoding="utf-8"))
        if taps_json is not None
        else None
    )
    sampler = CameraMotionSampler()
    window: deque[tuple[float, npt.NDArray[np.uint8], npt.NDArray[np.uint8]]] = deque(maxlen=3)
    times: list[float] = []
    chunks: list[npt.NDArray[np.void]] = []
    per_frame: list[int] = []
    calibration: CourtCalibration | None = None
    calibration_report: dict[str, Any] | None = None
    roi: npt.NDArray[np.uint8] | None = None
    diameter: Callable[[float, float], float] | None = None
    track_size: tuple[int, int] | None = None

    for index, (pts_s, bgr) in enumerate(iter_track_frames(video, probe)):
        if track_size is None:
            track_size = (int(bgr.shape[1]), int(bgr.shape[0]))
            if request is not None:
                # Taps are fractions of the video frame, so they map straight to
                # TRACK_SPACE; the request's frame size is only the aspect check.
                verdict = calibrate_court(
                    taps_in_track_space(request, track_size),
                    track_size,
                    video_frame_size=(request.frame_width_px, request.frame_height_px),
                )
                calibration = verdict.calibration
                calibration_report = {
                    "accepted": calibration is not None,
                    "rejection": None if verdict.rejection is None else verdict.rejection.value,
                }
                if calibration is not None:
                    roi = court_roi_mask(calibration, track_size)
                    diameter = expected_diameter_fn(calibration)
                    calibration_report.update(
                        calls_allowed=calibration.calls_allowed,
                        reprojection_rms_px=round(calibration.reprojection_rms_px, 3),
                        camera_height_m=round(calibration.pose.height_m, 3),
                        roi_frame_fraction=round(float(np.mean(roi > 0)), 4),
                    )
        times.append(pts_s)
        sampler.add(pts_s, bgr)
        window.append((pts_s, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), bgr))
        if len(window) == 3:
            (_, prev_gray, _), (cur_s, cur_gray, cur_bgr), (_, next_gray, _) = window
            found = find_flight_candidates(
                prev_gray,
                cur_gray,
                next_gray,
                cur_bgr,
                index - 1,
                cur_s,
                roi,
                diameter,
            )
            chunks.append(found)
            per_frame.append(len(found))

    frame_times = np.asarray(times, dtype=np.float64)
    candidates = np.concatenate(chunks) if chunks else np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)
    samples = sampler.result()
    stability = assess_camera_stability(
        samples.times_s, samples.shifts_px, samples.feature_counts, max(samples.long_edge_px, 1)
    )
    deltas = np.diff(frame_times)
    summary: dict[str, Any] = {
        "mode": "candidates",
        "video": video.name,
        "source": {
            "width_px": probe.width_px,
            "height_px": probe.height_px,
            "rotation_deg": probe.rotation_deg,
            "source_fps": round(probe.source_fps, 3),
            "duration_s": round(probe.duration_s, 3),
        },
        "frames_decoded": len(times),
        "track_frame_size": None if track_size is None else list(track_size),
        "pts_strictly_increasing": bool(np.all(deltas > 0.0)),
        "median_fps": round(1.0 / float(np.median(deltas)), 3) if deltas.size else None,
        "candidates_per_frame": count_summary(np.asarray(per_frame, dtype=np.int64)),
        "camera": {
            "verdict": stability.verdict.value,
            "p95_shift_fraction": stability.p95_shift_fraction,
            "rms_shift_px": stability.rms_shift_px,
            "bumped_at_s": stability.bumped_at_s,
            "samples": stability.samples,
            "median_feature_count": (
                float(np.median(samples.feature_counts)) if samples.feature_counts.size else None
            ),
        },
        "calibration": calibration_report,
        "labels": None,
    }
    if labels_jsonl is not None and track_size is not None:
        labels, session = parse_labels(labels_jsonl.read_text(encoding="utf-8").splitlines())
        label_long_edge = (
            max(session.width, session.height)
            if session is not None
            else max(probe.width_px, probe.height_px)
        )
        evaluated = set(range(1, len(times) - 1))
        total, matched = label_match(candidates, labels, max(track_size) / label_long_edge, evaluated)
        summary["labels"] = {
            "visible_on_evaluated_frames": total,
            "within_3_px": matched,
            "rate": round(matched / total, 4) if total else None,
        }
    summary["elapsed_s"] = round(time.monotonic() - started, 2)
    return summary, candidates, frame_times


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=["candidates"], default="candidates")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--taps-json", type=Path)
    parser.add_argument("--labels-jsonl", type=Path)
    parser.add_argument("--out", help="output name under backend/build/court_eval/")
    args = parser.parse_args(argv)

    destination = output_dir(args.out or args.video.stem)
    summary, candidates, frame_times = run_candidates(args.video, args.taps_json, args.labels_jsonl)
    destination.mkdir(parents=True, exist_ok=True)
    np.save(destination / "candidates.npy", candidates)
    np.save(destination / "frame_times.npy", frame_times)
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
