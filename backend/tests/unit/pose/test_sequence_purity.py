"""Banned-import rule (PIPELINE.md 4.4): `mediapipe` may not enter the pure seam.

`app/pose/extractor.py` is the only module permitted to import mediapipe; the
seam builder must be runnable with MediaPipe absent from the environment.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import app.models.internal as internal_module
import app.pose.sequence as sequence_module

PURE_MODULES = (sequence_module, internal_module)
BANNED = {
    "mediapipe",
    "av",
    "cv2",
    "httpx",
    "requests",
    "supabase",
    "pathlib",
    "socket",
    "time",
}


def imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module.split(".")[0])
    return names


def test_pure_modules_import_nothing_banned() -> None:
    for module in PURE_MODULES:
        path = Path(module.__file__)
        assert not (imported_names(path) & BANNED), f"{path.name} imports a banned module"


def test_importing_the_seam_does_not_pull_in_mediapipe() -> None:
    # Fresh interpreter: importing the pure module must leave sys.modules clean.
    code = (
        "import sys; import app.pose.sequence, app.models.internal; "
        "print('mediapipe' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[3]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


def test_seam_is_importable_with_mediapipe_unavailable() -> None:
    # Simulate an environment without mediapipe: any import of it must fail,
    # yet the seam module must still import and build a sequence.
    code = """
import builtins, sys
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] == 'mediapipe':
        raise ImportError('mediapipe is unavailable in this environment')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import numpy as np
from app.models.internal import RawPoseSequence
from app.pose.sequence import build_pose_sequence, detection_rate
raw = RawPoseSequence(
    landmarks=np.zeros((2, 33, 4), dtype=np.float32),
    world=np.zeros((2, 33, 3), dtype=np.float32),
    timestamps_s=np.zeros((2,), dtype=np.float64),
    detected=np.ones((2,), dtype=bool),
)
seq = build_pose_sequence(raw, 640, 360)
print(seq.landmarks.shape, detection_rate(seq.detected), 'mediapipe' in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[3]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "(2, 33, 4) 1.0 False"
