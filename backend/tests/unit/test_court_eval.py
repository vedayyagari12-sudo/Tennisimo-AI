"""Batch E: ``scripts/court_eval.py --mode candidates``.

Its numeric helpers (label matching, count summary, tap conversion) and an
end-to-end run on a small synthetic clip. The vendored and real-clip runs are
smoke runs from the command line, not tests (no assertion on real footage).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.analysis.flight_track import FLIGHT_CANDIDATE_DTYPE
from app.models.court import CourtCalibrationRequest, CourtKeypoint
from scripts import court_eval
from scripts.court_eval import (
    BallLabel,
    count_summary,
    label_match,
    output_dir,
    parse_labels,
    run_candidates,
    taps_in_track_space,
)
from tests.unit.analysis.synthetic_court import RECOMMENDED_SIX, keypoint_array, look_at_camera
from tests.unit.seam_clips import write_clip

WIDTH, HEIGHT, FPS = 320, 96, 25
MOVE_START, STEP_PX = 40, 24


def moving_centre(frame_index: int, frame_count: int, width: int, height: int) -> tuple[int, int]:
    """Parked, then crossing the frame at 24 px per frame, then parked."""
    steps = min(max(frame_index - MOVE_START, 0), 11)
    return 30 + STEP_PX * steps, height // 2


def candidate_rows(rows: list[tuple[int, float, float]]) -> np.ndarray:
    out = np.zeros(len(rows), dtype=FLIGHT_CANDIDATE_DTYPE)
    for i, (frame, x, y) in enumerate(rows):
        out[i]["frame_index"], out[i]["x"], out[i]["y"] = frame, x, y
    return out


def test_output_dir_takes_plain_names_only() -> None:
    assert output_dir("clip_01.v2") == court_eval.OUTPUT_ROOT / "clip_01.v2"
    for bad in ("../escape", "a/b", "a\\b", "", ".hidden", "x..y"):
        with pytest.raises(ValueError):
            output_dir(bad)


def test_parse_labels_reads_ball_and_session_lines_only() -> None:
    lines = [
        '{"type":"session","session_id":"s01","width":1920,"height":1080,"fps":59.94}',
        '{"type":"ball","frame":5100,"u_px":840.0,"v_px":300.0,"visible":true}',
        '{"type":"bounce","frame":5120,"u_px":812.0,"v_px":231.5,"end":"far"}',
        "",
        '{"type":"ball","frame":5101,"u_px":845.5,"v_px":301.0,"visible":false}',
    ]
    balls, session = parse_labels(lines)
    assert [b.frame for b in balls] == [5100, 5101]
    assert balls[1].visible is False
    assert session is not None and (session.width, session.height) == (1920, 1080)


def test_label_match_counts_within_3_label_pixels() -> None:
    """Track space is 2x the labelled frame: track (200, 100) is label (100, 50)."""
    candidates = candidate_rows([(10, 200.0, 100.0), (11, 400.0, 100.0), (11, 207.0, 100.0)])
    labels = [
        BallLabel(type="ball", frame=10, u_px=102.9, v_px=50.0, visible=True),  # 2.9 px: in
        BallLabel(type="ball", frame=11, u_px=100.0, v_px=50.0, visible=True),  # 3.5 px: out
        BallLabel(type="ball", frame=12, u_px=100.0, v_px=50.0, visible=True),  # no candidates
        BallLabel(type="ball", frame=10, u_px=0.0, v_px=0.0, visible=False),  # not counted
        BallLabel(type="ball", frame=99, u_px=100.0, v_px=50.0, visible=True),  # not evaluated
    ]
    assert label_match(candidates, labels, 2.0, {10, 11, 12}) == (3, 1)


def test_count_summary() -> None:
    summary = count_summary(np.array([0, 2, 2, 4, 12], dtype=np.int64))
    assert summary == {"frames_evaluated": 5, "total": 20, "mean": 4.0, "median": 2.0,
                       "p95": pytest.approx(10.4), "max": 12}
    assert count_summary(np.zeros(0, dtype=np.int64))["frames_evaluated"] == 0


def test_taps_are_fractions_of_the_track_frame() -> None:
    request = CourtCalibrationRequest.model_validate(
        {"frame_time_s": 1.0, "frame_width_px": 1280, "frame_height_px": 720,
         "court_format": "singles",
         "taps": [{"keypoint": "near_service_t", "x": 0.5, "y": 0.75}]}
    )
    (tap,) = taps_in_track_space(request, (1920, 1080))
    assert (tap.keypoint, tap.u_px, tap.v_px) == (CourtKeypoint.NEAR_SERVICE_T, 960.0, 810.0)


def test_end_to_end_on_a_synthetic_clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = write_clip(tmp_path / "moving.mp4", frame_count=80, width=WIDTH, height=HEIGHT,
                      fps=FPS, centre_fn=moving_centre)
    labels = tmp_path / "labels.jsonl"
    lines = [json.dumps({"type": "session", "width": WIDTH, "height": HEIGHT})]
    for frame in range(MOVE_START + 1, MOVE_START + 11):
        u, v = moving_centre(frame, 80, WIDTH, HEIGHT)
        lines.append(json.dumps({"type": "ball", "frame": frame, "u_px": u, "v_px": v, "visible": True}))
    labels.write_text("\n".join(lines), encoding="utf-8")

    summary, candidates, frame_times = run_candidates(clip, None, labels)
    assert summary["frames_decoded"] == 80 and len(frame_times) == 80
    assert summary["pts_strictly_increasing"] is True
    assert summary["median_fps"] == pytest.approx(25.0)
    assert summary["track_frame_size"] == [WIDTH, HEIGHT]
    assert summary["candidates_per_frame"]["frames_evaluated"] == 78
    assert summary["labels"] == {"visible_on_evaluated_frames": 10, "within_3_px": 10, "rate": 1.0}
    assert summary["calibration"] is None
    assert summary["camera"]["verdict"] in {"stable", "shaky", "moving", "unknown"}
    assert candidates.dtype == FLIGHT_CANDIDATE_DTYPE
    assert set(candidates["frame_index"].tolist()) <= set(range(1, 79))

    monkeypatch.setattr(court_eval, "OUTPUT_ROOT", tmp_path / "build")
    assert court_eval.main(["--video", str(clip), "--out", "synthetic"]) == 0
    written = tmp_path / "build" / "synthetic"
    assert json.loads((written / "summary.json").read_text(encoding="utf-8"))["frames_decoded"] == 80
    assert np.load(written / "candidates.npy").dtype == FLIGHT_CANDIDATE_DTYPE
    assert np.load(written / "frame_times.npy").shape == (80,)


def test_taps_give_a_calibration_roi_and_size_gate(tmp_path: Path) -> None:
    """Eight taps from a known camera at the clip's 320x180: accepted, calls
    allowed (G21), ROI built. Three taps: rejected, the run continues uncalibrated."""
    camera = look_at_camera(focal_px=1507.0 / 6.0, frame_size=(320, 180))
    eight = (*RECOMMENDED_SIX, CourtKeypoint.NEAR_LEFT_SERVICE_CORNER, CourtKeypoint.NEAR_RIGHT_SERVICE_CORNER)
    uv = camera.project_ground(keypoint_array(list(eight)))
    body = {
        "frame_time_s": 0.0, "frame_width_px": 1920, "frame_height_px": 1080, "court_format": "singles",
        "taps": [
            {"keypoint": k.value, "x": float(u) / 320.0, "y": float(v) / 180.0}
            for k, (u, v) in zip(eight, uv, strict=True)
        ],
    }
    taps = tmp_path / "taps.json"
    taps.write_text(json.dumps(body), encoding="utf-8")
    clip = write_clip(tmp_path / "court.mp4", frame_count=60, width=320, height=180, fps=FPS,
                      centre_fn=moving_centre)

    summary, _, _ = run_candidates(clip, taps, None)
    report = summary["calibration"]
    assert report["accepted"] is True and report["rejection"] is None
    assert report["calls_allowed"] is True
    assert report["camera_height_m"] == pytest.approx(3.5, abs=1e-3)
    assert report["roi_frame_fraction"] == 1.0  # the recommended placement: see flight_detector

    body["taps"] = body["taps"][:3]
    taps.write_text(json.dumps(body), encoding="utf-8")
    rejected, _, _ = run_candidates(clip, taps, None)
    assert rejected["calibration"] == {"accepted": False, "rejection": "too_few_taps"}
    assert rejected["frames_decoded"] == 60
