"""Startup warm-up of the analysis pipeline import.

The pipeline import (MediaPipe, PyAV, OpenCV) used to happen lazily inside the
first job after every cold start, on the critical path. `app.main` now runs it
on a background thread at startup. It is best-effort: a failure must be logged
and swallowed, never take the server down.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

import app.main as main
from app.config import Settings

FAKE_ENV = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}


def test_warm_up_is_on_by_default() -> None:
    assert Settings(env=FAKE_ENV).warm_pipeline_on_startup is True


def test_warm_up_can_be_switched_off() -> None:
    settings = Settings(env={**FAKE_ENV, "WARM_PIPELINE_ON_STARTUP": "false"})
    assert settings.warm_pipeline_on_startup is False


def test_warm_up_imports_the_pipeline_module(monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []

    def fake_import(name: str) -> Any:
        imported.append(name)
        return object()

    monkeypatch.setattr(main.importlib, "import_module", fake_import)
    main._warm_pipeline()
    assert imported == ["app.services.pipeline"]


def test_a_failed_warm_up_is_logged_and_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_import(name: str) -> Any:
        raise RuntimeError("simulated import failure")

    monkeypatch.setattr(main.importlib, "import_module", broken_import)
    with caplog.at_level(logging.ERROR, logger="tennisform"):
        main._warm_pipeline()  # must not raise
    assert any("warm-up failed" in record.getMessage() for record in caplog.records)
