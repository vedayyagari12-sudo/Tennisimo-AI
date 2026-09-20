"""`Settings` — the plain-class replacement for PIPELINE.md §3.4's BaseSettings.

Every value in this file is obviously fake. No real URL, key or project ref.
"""

from __future__ import annotations

import pytest

from app.config import MAX_UPLOAD_BYTES, MissingSettingError, Settings

FAKE_ENV = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}


def test_defaults_match_pipeline_section_3_4() -> None:
    settings = Settings(env=FAKE_ENV)
    assert settings.max_upload_bytes == MAX_UPLOAD_BYTES == 52_428_800
    assert settings.max_clip_seconds == 60.0
    assert settings.analysis_fps == 30.0
    assert settings.max_analysis_frames == 240
    assert settings.target_long_edge_px == 640
    assert settings.job_queue_max_depth == 4
    assert settings.job_heartbeat_stale_s == 180
    assert settings.supabase_jwt_audience == "authenticated"
    assert settings.supabase_storage_bucket == "swing-videos"
    assert settings.gemini_model == "gemini-2.5-flash"
    assert settings.ball_detection_enabled is True
    assert settings.ball_speed_min_mph == 15
    assert settings.ball_speed_max_mph == 160


@pytest.mark.parametrize("missing", sorted(FAKE_ENV))
def test_a_missing_required_variable_names_itself(missing: str) -> None:
    env = {k: v for k, v in FAKE_ENV.items() if k != missing}
    with pytest.raises(MissingSettingError) as excinfo:
        Settings(env=env)
    assert excinfo.value.name == missing
    assert missing in str(excinfo.value)


def test_an_empty_required_variable_counts_as_missing() -> None:
    with pytest.raises(MissingSettingError):
        Settings(env={**FAKE_ENV, "SUPABASE_URL": "   "})


def test_issuer_and_jwks_url_are_derived_from_supabase_url() -> None:
    settings = Settings(env=FAKE_ENV)
    assert settings.supabase_jwt_issuer == "https://fake-project.supabase.invalid/auth/v1"
    assert settings.supabase_jwks_url == (
        "https://fake-project.supabase.invalid/auth/v1/.well-known/jwks.json"
    )


def test_trailing_slash_on_supabase_url_is_normalized() -> None:
    settings = Settings(env={**FAKE_ENV, "SUPABASE_URL": "https://fake.invalid/"})
    assert settings.supabase_url == "https://fake.invalid"
    assert settings.supabase_jwt_issuer == "https://fake.invalid/auth/v1"


def test_secrets_are_not_exposed_by_repr() -> None:
    settings = Settings(env=FAKE_ENV)
    assert "fake-service-role-key-not-a-secret" not in repr(settings)
    assert "fake-service-role-key-not-a-secret" not in repr(settings.supabase_service_role_key)
    assert settings.supabase_service_role_key.get_secret_value() == FAKE_ENV[
        "SUPABASE_SERVICE_ROLE_KEY"
    ]


def test_overrides_are_read_from_the_environment() -> None:
    settings = Settings(env={**FAKE_ENV, "JOB_QUEUE_MAX_DEPTH": "7", "ANALYSIS_FPS": "24.0"})
    assert settings.job_queue_max_depth == 7
    assert settings.analysis_fps == 24.0


def test_a_non_integer_override_is_a_boot_failure_naming_the_variable() -> None:
    with pytest.raises(RuntimeError, match="JOB_QUEUE_MAX_DEPTH"):
        Settings(env={**FAKE_ENV, "JOB_QUEUE_MAX_DEPTH": "four"})


def test_boolean_override_accepts_the_usual_spellings() -> None:
    assert Settings(env={**FAKE_ENV, "BALL_DETECTION_ENABLED": "false"}).ball_detection_enabled is False
    assert Settings(env={**FAKE_ENV, "BALL_DETECTION_ENABLED": "0"}).ball_detection_enabled is False
    assert Settings(env={**FAKE_ENV, "BALL_DETECTION_ENABLED": "yes"}).ball_detection_enabled is True
