"""Court mode evaluation: ``--mode candidates`` (brief batch E) and
``--mode events`` (build step 16: the whole engine, wire JSON in court.json).

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

from app.analysis.ball_events import PoseHit  # noqa: E402
from app.analysis.court_session import analyze_court_session  # noqa: E402
from app.ball.flight_detector import court_roi_mask  # noqa: E402
from app.models.court import CourtCalibrationRequest, CourtEnd, CourtFormat  # noqa: E402
from app.services.court_offline import (  # noqa: E402
    taps_in_track_space,
    CourtVideoInputs,
    extract_court_inputs,
    project_court_result,
)

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


def load_request(taps_json: Path | None) -> CourtCalibrationRequest | None:
    if taps_json is None:
        return None
    return CourtCalibrationRequest.model_validate_json(taps_json.read_text(encoding="utf-8"))


def calibration_summary(inputs: CourtVideoInputs) -> dict[str, Any] | None:
    verdict = inputs.calibration_verdict
    if verdict is None:
        return None
    calibration = verdict.calibration
    report: dict[str, Any] = {
        "accepted": calibration is not None,
        "rejection": None if verdict.rejection is None else verdict.rejection.value,
    }
    if calibration is not None and inputs.track_size is not None:
        roi = court_roi_mask(calibration, inputs.track_size)
        report.update(
            calls_allowed=calibration.calls_allowed,
            reprojection_rms_px=round(calibration.reprojection_rms_px, 3),
            camera_height_m=round(calibration.pose.height_m, 3),
            roi_frame_fraction=round(float(np.mean(roi > 0)), 4),
        )
    return report


def run_candidates(
    video: Path, taps_json: Path | None, labels_jsonl: Path | None
) -> tuple[dict[str, Any], npt.NDArray[np.void], npt.NDArray[np.float64]]:
    started = time.monotonic()
    inputs = extract_court_inputs(video, load_request(taps_json))
    probe, track_size, stability = inputs.probe, inputs.track_size, inputs.stability
    candidates, frame_times = inputs.candidates, inputs.frame_times_s
    times = list(frame_times)
    per_frame = list(inputs.per_frame_counts)
    calibration_report = calibration_summary(inputs)
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
            "median_feature_count": inputs.median_feature_count,
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


class PoseHitLine(BaseModel):
    """One ``--pose-hits-json`` entry: a contact from the swing pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    time_s: float
    wrist_u_px: float | None = None
    wrist_v_px: float | None = None
    end: CourtEnd | None = None
    confidence: float = 1.0
    shot_type: str | None = None


def load_pose_hits(path: Path | None) -> tuple[PoseHit, ...]:
    if path is None:
        return ()
    hits = []
    for row in json.loads(path.read_text(encoding="utf-8")):
        line = PoseHitLine.model_validate(row)
        wrist = (
            None
            if line.wrist_u_px is None or line.wrist_v_px is None
            else (line.wrist_u_px, line.wrist_v_px)
        )
        hits.append(PoseHit(line.time_s, wrist, line.end, line.confidence, line.shot_type))
    return tuple(hits)


def run_events(
    video: Path, taps_json: Path | None, pose_hits_json: Path | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Step 16: the whole court engine over one clip. Returns (summary, wire)."""
    started = time.monotonic()
    request = load_request(taps_json)
    inputs = extract_court_inputs(video, request)
    calibration = (
        inputs.calibration_verdict.calibration if inputs.calibration_verdict is not None else None
    )
    frame_size = inputs.track_size or (1, 1)
    result = analyze_court_session(
        inputs.candidates,
        inputs.frame_times_s,
        calibration,
        inputs.stability,
        load_pose_hits(pose_hits_json),
        court_format=request.court_format if request is not None else CourtFormat.SINGLES,
        frame_size=frame_size,
    )
    wire = project_court_result(result, frame_size=frame_size)
    summary: dict[str, Any] = {
        "mode": "events",
        "video": video.name,
        "frames_decoded": int(inputs.frame_times_s.size),
        "status": wire.status.value,
        "refusal_reason": None if wire.refusal_reason is None else wire.refusal_reason.value,
        "camera": wire.camera.verdict.value,
        "calibration": calibration_summary(inputs),
        "flights": len(result.flights),
        "events": {
            kind: sum(1 for e in result.events if e.kind.value == kind)
            for kind in sorted({e.kind.value for e in result.events})
        },
        "landings": wire.summary.count,
        "calls": {k.value: v for k, v in wire.summary.verdict_counts.items()},
        "rallies": len(wire.rallies),
        "elapsed_s": round(time.monotonic() - started, 2),
    }
    return summary, wire.model_dump(mode="json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=["candidates", "events"], default="candidates")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--taps-json", type=Path)
    parser.add_argument("--labels-jsonl", type=Path)
    parser.add_argument("--pose-hits-json", type=Path, help="events mode: list of pose hits")
    parser.add_argument("--out", help="output name under backend/build/court_eval/")
    args = parser.parse_args(argv)

    destination = output_dir(args.out or args.video.stem)
    if args.mode == "events":
        events_summary, wire = run_events(args.video, args.taps_json, args.pose_hits_json)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "court.json").write_text(json.dumps(wire, indent=2), encoding="utf-8")
        (destination / "summary.json").write_text(
            json.dumps(events_summary, indent=2), encoding="utf-8"
        )
        print(json.dumps(events_summary, indent=2))
        print(f"wrote {destination}")
        return 0
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
