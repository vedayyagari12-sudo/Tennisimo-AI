"""Banned-import rule for `app/analysis/**` (PIPELINE.md 4.4).

Documentation about purity decays; a failing test does not. The cv2 ban in
particular is what stops "ball detection needs OpenCV" from becoming "the
metrics module imports cv2 for one convenience function".
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ANALYSIS_DIR = Path(__file__).resolve().parents[3] / "app" / "analysis"
REPO_BACKEND = Path(__file__).resolve().parents[3]

BANNED_TOP_LEVEL = {
    "mediapipe",
    "av",
    "cv2",
    "httpx",
    "requests",
    "supabase",
    "os",
    "pathlib",
    "socket",
    "time",
}
BANNED_DOTTED = {
    "google.generativeai",
    "app.services",
    "app.pose.extractor",
    "app.pose.video_io",
    "app.ball.frames",
    "app.ball.detector",
}


def analysis_modules() -> list[Path]:
    modules = sorted(ANALYSIS_DIR.glob("*.py"))
    assert modules, "app/analysis contains no modules"
    return modules


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_analysis_package_imports_nothing_banned() -> None:
    for module in analysis_modules():
        imported = imported_modules(module)
        top_level = {name.split(".")[0] for name in imported}
        assert not (top_level & BANNED_TOP_LEVEL), f"{module.name} imports a banned module"
        for dotted in BANNED_DOTTED:
            assert not any(
                name == dotted or name.startswith(dotted + ".") for name in imported
            ), f"{module.name} imports {dotted}"


def test_analysis_package_calls_no_file_io() -> None:
    for module in analysis_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "open" not in called, f"{module.name} calls open()"


def test_importing_analysis_does_not_pull_in_mediapipe_or_cv2() -> None:
    code = (
        "import sys; import app.analysis.contact, app.analysis.handedness, "
        "app.analysis.normalize, app.analysis.smoothing; "
        "print(any(m in sys.modules for m in ('mediapipe', 'cv2', 'av')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_BACKEND),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
