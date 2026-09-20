"""Application settings, read from the process environment.

DELIBERATE DEVIATION FROM PIPELINE.md §3.4
------------------------------------------
§3.4 specifies ``class Settings(BaseSettings)``. In Pydantic v2 ``BaseSettings``
lives in the separate ``pydantic-settings`` distribution, and **that package is
not installed in this environment**. Rather than add a dependency for a class
that reads ``os.environ`` and raises on a missing key, this module implements
``Settings`` as a plain typed class that does exactly that:

* every field carries an explicit type annotation;
* required fields are read with ``_require()`` and raise ``MissingSettingError``
  naming the offending variable;
* defaulted fields are read with the ``_str``/``_int``/``_float``/``_bool``
  helpers and state their default inline, so "required" vs "defaulted" is
  visible at a glance;
* secrets are wrapped in ``pydantic.SecretStr`` exactly as §3.4 specifies, so
  they do not leak into a repr or a log line.

The field names, types and default values are otherwise identical to §3.4.
Swapping this for ``BaseSettings`` later is a drop-in change: the public surface
is ``Settings(...)`` attribute access plus ``get_settings()``.

No secret, URL, project reference or token literal appears in this file.
Everything comes from the environment (CLAUDE.md).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Final

from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Module constants that other modules must not re-derive.
# ---------------------------------------------------------------------------

#: 50 MiB. PIPELINE.md §0.3, §2.2, §3.4 and DATABASE_SETUP.md Part 3.1.
#: This is THE single source for the upload cap: it is the default of
#: ``Settings.max_upload_bytes`` and the ``le=`` bound used in
#: ``app.models.requests``. Matches the Supabase free-tier per-file limit.
MAX_UPLOAD_BYTES: Final[int] = 52_428_800

#: Base ETA in seconds for one job (PIPELINE.md §1.20 — deliberately padded).
#: DATABASE_SETUP.md Part 3.2: ``estimated_seconds`` is this plus roughly this
#: much again per job already ahead in the queue.
JOB_ESTIMATED_SECONDS_BASE: Final[int] = 25

#: ``Retry-After`` seconds sent with ``429 queue_full``. DATABASE_SETUP.md
#: Part 3.0.1 / Part 6 item 19 — the number is invented there ("roughly one
#: job") and is repeated here rather than re-invented at the call site.
QUEUE_FULL_RETRY_AFTER_S: Final[int] = 30

#: ``GET /v1/analyses`` paging bounds (DATABASE_SETUP.md Part 3.4).
HISTORY_DEFAULT_LIMIT: Final[int] = 50
HISTORY_MAX_LIMIT: Final[int] = 100

#: Storage prefix every object lives under. The first path segment after it is
#: the owner's uuid, which is what makes the Stage 3 ownership check a prefix
#: comparison (DATABASE_SETUP.md Part 2.1).
STORAGE_BUCKET_DEFAULT: Final[str] = "swing-videos"


class MissingSettingError(RuntimeError):
    """A required environment variable is absent or empty.

    Carries the variable name so the operator is told *which* one, rather than
    being handed a ``KeyError`` traceback.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Required environment variable {name} is not set (or is empty). "
            f"Set {name} before starting the service."
        )
        self.name: str = name


def _require(env: Mapping[str, str], name: str) -> str:
    """Return ``env[name]`` or raise naming the variable."""
    value = env.get(name, "").strip()
    if not value:
        raise MissingSettingError(name)
    return value


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, "").strip()
    return value if value else default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - operator error path
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw!r}.") from exc


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - operator error path
        raise RuntimeError(f"Environment variable {name} must be a number, got {raw!r}.") from exc


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Environment variable {name} must be a boolean, got {raw!r}.")


class Settings:
    """Typed process configuration. See the module docstring for the §3.4 deviation."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        source: Mapping[str, str] = os.environ if env is None else env

        # --- REQUIRED (no default; absence is a boot failure) --------------
        self.supabase_url: str = _require(source, "SUPABASE_URL").rstrip("/")
        self.supabase_service_role_key: SecretStr = SecretStr(
            _require(source, "SUPABASE_SERVICE_ROLE_KEY")
        )
        self.gemini_api_key: SecretStr = SecretStr(_require(source, "GEMINI_API_KEY"))

        # --- DEFAULTED ------------------------------------------------------
        # §3.4 lists supabase_jwks_url with no default, which would make it a
        # required variable. It is derived instead, because Stage 2 states the
        # exact URL ("{SUPABASE_URL}/auth/v1/.well-known/jwks.json") and an
        # operator who sets it to anything else has broken auth. Still
        # overridable by SUPABASE_JWKS_URL for a test double.
        self.supabase_jwks_url: str = _str(
            source,
            "SUPABASE_JWKS_URL",
            f"{self.supabase_url}/auth/v1/.well-known/jwks.json",
        )
        self.supabase_jwt_audience: str = _str(source, "SUPABASE_JWT_AUDIENCE", "authenticated")
        #: Stage 2: issuer is f"{SUPABASE_URL}/auth/v1". Derived, not configured.
        self.supabase_jwt_issuer: str = f"{self.supabase_url}/auth/v1"
        self.supabase_storage_bucket: str = _str(
            source, "SUPABASE_STORAGE_BUCKET", STORAGE_BUCKET_DEFAULT
        )

        self.gemini_model: str = _str(source, "GEMINI_MODEL", "gemini-2.5-flash")
        self.gemini_timeout_s: float = _float(source, "GEMINI_TIMEOUT_S", 8.0)

        self.max_upload_bytes: int = _int(source, "MAX_UPLOAD_BYTES", MAX_UPLOAD_BYTES)
        self.max_clip_seconds: float = _float(source, "MAX_CLIP_SECONDS", 60.0)
        self.analysis_window_seconds: float = _float(source, "ANALYSIS_WINDOW_SECONDS", 8.0)
        self.analysis_fps: float = _float(source, "ANALYSIS_FPS", 30.0)
        self.max_analysis_frames: int = _int(source, "MAX_ANALYSIS_FRAMES", 240)
        self.target_long_edge_px: int = _int(source, "TARGET_LONG_EDGE_PX", 640)
        self.motion_scan_threshold_s: float = _float(source, "MOTION_SCAN_THRESHOLD_S", 10.0)
        self.motion_scan_long_edge_px: int = _int(source, "MOTION_SCAN_LONG_EDGE_PX", 160)
        self.job_queue_max_depth: int = _int(source, "JOB_QUEUE_MAX_DEPTH", 4)
        self.job_heartbeat_stale_s: int = _int(source, "JOB_HEARTBEAT_STALE_S", 180)

        # --- v2: ball speed -------------------------------------------------
        self.ball_detection_enabled: bool = _bool(source, "BALL_DETECTION_ENABLED", True)
        self.ball_cal_space_long_edge_px: int = _int(source, "BALL_CAL_SPACE_LONG_EDGE_PX", 1280)
        self.ball_window_post_s: float = _float(source, "BALL_WINDOW_POST_S", 0.25)
        self.ball_background_preroll_s: float = _float(source, "BALL_BACKGROUND_PREROLL_S", 0.50)
        self.ball_background_gap_s: float = _float(source, "BALL_BACKGROUND_GAP_S", 0.05)
        self.ball_background_frames: int = _int(source, "BALL_BACKGROUND_FRAMES", 15)
        self.ball_min_detections: int = _int(source, "BALL_MIN_DETECTIONS", 3)
        self.ball_detection_deadline_s: float = _float(source, "BALL_DETECTION_DEADLINE_S", 6.0)
        self.ball_speed_min_mph: int = _int(source, "BALL_SPEED_MIN_MPH", 15)
        self.ball_speed_max_mph: int = _int(source, "BALL_SPEED_MAX_MPH", 160)

        # --- HTTP client / storage plumbing ---------------------------------
        # NOT in §3.4. Lifetime of a Supabase signed *upload* URL. The value is
        # invented (Supabase's documented default for upload tickets is two
        # hours); it is a Settings field rather than a literal so an operator
        # can shorten it without a redeploy.
        self.signed_upload_url_ttl_s: int = _int(source, "SIGNED_UPLOAD_URL_TTL_S", 7200)
        # NOT in §3.4. Timeout for the backend's own Supabase calls.
        self.supabase_http_timeout_s: float = _float(source, "SUPABASE_HTTP_TIMEOUT_S", 10.0)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Settings(supabase_url={self.supabase_url!r}, bucket={self.supabase_storage_bucket!r})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached because reading and validating the environment on every request is
    pointless work, and because ``lru_cache`` gives tests a documented reset
    hook (``get_settings.cache_clear()``).
    """
    return Settings()
