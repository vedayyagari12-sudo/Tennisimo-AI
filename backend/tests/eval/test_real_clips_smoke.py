"""Real-clip smoke run (SESSION_MODE_PHASE2.md 11.3, build step 18).

Marker ``real_clips``; skipped unless ``TENNIS_CLIPS_DIRS`` lists the local clip
folders (``;``-separated). Clips are never committed; the manifest holds file
names and measured expectations only.

These 16 clips cannot validate in/out, depth, rallies or scoring: none has a
fixed full-court camera or a whole rally. They prove the decode and PTS path,
the camera-motion verdict, and that nothing is called or counted without a
calibration. Do NOT add accuracy assertions here.

Court fit (step 19) is checked on each clip's FIRST frame against the
manifest's ``max_fits``: zero on padel and courtless clips, at most 3 (each
needing confirmation) on the others.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from app.models.court import CourtSessionStatus, StabilityVerdict

MANIFEST = Path(__file__).with_name("real_clip_manifest.json")
DIRS_ENV = "TENNIS_CLIPS_DIRS"


def clip_dirs() -> list[Path]:
    raw = os.environ.get(DIRS_ENV, "")
    return [Path(p) for p in raw.split(";") if p.strip()]


def manifest() -> dict[str, dict[str, object]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["clips"]


def find(name: str) -> Path | None:
    for directory in clip_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def test_manifest_holds_file_names_only() -> None:
    """Runs everywhere: no paths, no frames, no labels in the repo."""
    for name, expectation in manifest().items():
        assert "/" not in name and "\\" not in name
        assert name.endswith(".mp4")
        assert set(expectation) <= {"camera", "stability_verdict", "fps", "note", "max_fits"}


@pytest.mark.real_clips
@pytest.mark.parametrize("name", sorted(manifest()))
def test_real_clip_smoke(name: str) -> None:
    if not clip_dirs():
        pytest.skip(f"{DIRS_ENV} is not set")
    path = find(name)
    if path is None:
        pytest.skip(f"{name} not found under {DIRS_ENV}")
    from app.services.court_offline import analyze_court_video, project_court_result

    expectation = manifest()[name]
    if "max_fits" in expectation:
        from app.ball.court_lines import fit_court_from_frame
        from app.ball.flight_frames import iter_track_frames, probe_court_clip

        frames = iter_track_frames(path, probe_court_clip(path))
        _, first = next(frames)
        frames.close()
        fits = fit_court_from_frame(first)
        assert len(fits) <= int(expectation["max_fits"])  # type: ignore[call-overload]
    if "camera" not in expectation:
        return  # camera not measured for this clip yet
    inputs, result = analyze_court_video(path, None)
    times = inputs.frame_times_s

    # Decoded, with strictly increasing real PTS (25-60 fps, portrait included).
    assert times.size > 0
    assert bool(np.all(np.diff(times) > 0.0))

    # The camera verdict the builder measured and froze for this clip.
    verdict = inputs.stability.verdict
    if expectation["camera"] == "moving":
        assert verdict in (StabilityVerdict.MOVING, StabilityVerdict.UNKNOWN)
    elif expectation["camera"] == "fixed":
        assert verdict in (StabilityVerdict.STABLE, StabilityVerdict.SHAKY)
    else:
        assert verdict is StabilityVerdict.UNKNOWN

    # No calibration: refused, so no line call, no bounce in metres, no rally.
    wire = project_court_result(result, frame_size=inputs.track_size or (1, 1))
    assert wire.status is CourtSessionStatus.REFUSED
    assert wire.landings == []
    assert all(r.length_hits < 3 for r in wire.rallies)
