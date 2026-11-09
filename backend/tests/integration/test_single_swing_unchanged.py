"""Brief batch M, step 24: single-swing output is unchanged by Phase 2.

Runs ``run_analysis_job`` on the vendored serve clip with the exact settings and
fakes of ``completed_run`` in ``test_pipeline_end_to_end.py`` and compares every
numeric leaf, by JSON path, with the golden captured at step 0 (d0e4df5) before
any Phase 2 or Session Mode code existed. ``slow``: real decode and MediaPipe.

If this branch is ever rebased onto a newer main, recapture the golden first
(brief G22): it reflects the f985ff1 pipeline.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.services.pipeline import run_analysis_job
from tests.integration.test_pipeline_end_to_end import (
    RUN_ENV,
    ClipStorage,
    RecordingRepository,
    ReplayGeminiClient,
    make_context,
)

GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "golden"
    / "single_swing_serve_vertical_10340710.json"
)


def flatten(node: Any, excluded: frozenset[str], path: str = "") -> dict[str, Any]:
    """Numeric and null leaves by JSON path; bools and strings are skipped."""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, child in node.items():
            if key in excluded:
                continue
            out.update(flatten(child, excluded, f"{path}.{key}" if path else key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            out.update(flatten(child, excluded, f"{path}[{index}]"))
    elif node is None or (isinstance(node, (int, float)) and not isinstance(node, bool)):
        out[path] = node
    return out


@pytest.mark.slow
def test_single_swing_pipeline_outputs_unchanged() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    repository = RecordingRepository()
    client = ReplayGeminiClient()
    context = make_context(repository, ClipStorage(), Settings(env=RUN_ENV))
    run_analysis_job(context, gemini_client=client)
    assert repository.rows, "the runner persisted nothing"

    actual = flatten(repository.rows[0].payload, frozenset(golden["excluded_keys"]))
    expected: dict[str, Any] = golden["numbers"]
    shared = sorted(set(expected) & set(actual))
    missing = sorted(set(expected) - set(actual))
    assert not missing, f"golden paths no longer produced: {missing[:10]}"
    changed = [
        path
        for path in shared
        if not (
            expected[path] == actual[path]
            or (
                isinstance(expected[path], float)
                and isinstance(actual[path], (int, float))
                and math.isclose(expected[path], actual[path], rel_tol=1e-9, abs_tol=1e-12)
            )
        )
    ]
    assert not changed, {p: (expected[p], actual[p]) for p in changed[:10]}
