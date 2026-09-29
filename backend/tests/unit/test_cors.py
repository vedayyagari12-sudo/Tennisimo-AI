"""The browser allowlist: `parse_cors_origins` and how `create_app` wires it.

Two properties are worth a test more than the happy path is:

1. **Fail closed.** An unset `CORS_ALLOWED_ORIGINS` must produce an EMPTY
   allowlist, not `["*"]`. A wildcard default is the kind of mistake that is
   invisible in every test that does not look for it and permanent once
   deployed.
2. **Never `*` with credentials.** Browsers reject that pair outright, so a
   configuration that produced it would fail at runtime in the client, not
   here. `allow_credentials` is asserted False against the real middleware
   instance rather than inspected by eye.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from app.config import (
    CORS_ALLOWED_HEADERS,
    CORS_ALLOWED_METHODS,
    CORS_ALLOWED_ORIGINS_ENV,
    CORS_EXPOSED_HEADERS,
    get_settings,
    parse_cors_origins,
)
from app.main import create_app

FAKE_ENV = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}

ALLOWED_ORIGIN = "https://app.example.invalid"


# --------------------------------------------------------------------------- #
# parse_cors_origins
# --------------------------------------------------------------------------- #


def test_unset_variable_yields_an_empty_allowlist_not_a_wildcard() -> None:
    assert parse_cors_origins(None) == []


@pytest.mark.parametrize("raw", ["", "   ", ",", ",,", " , , "])
def test_empty_and_blank_only_values_yield_an_empty_allowlist(raw: str) -> None:
    assert parse_cors_origins(raw) == []


def test_a_single_origin_is_returned_unchanged() -> None:
    assert parse_cors_origins("https://app.example.invalid") == ["https://app.example.invalid"]


def test_multiple_origins_keep_their_order() -> None:
    raw = "https://a.invalid,https://b.invalid,http://localhost:5173"
    assert parse_cors_origins(raw) == [
        "https://a.invalid",
        "https://b.invalid",
        "http://localhost:5173",
    ]


def test_surrounding_whitespace_is_tolerated_on_every_entry() -> None:
    raw = "  https://a.invalid ,\thttps://b.invalid\n,   http://localhost:5173  "
    assert parse_cors_origins(raw) == [
        "https://a.invalid",
        "https://b.invalid",
        "http://localhost:5173",
    ]


def test_empty_entries_between_commas_are_dropped_not_kept_as_blanks() -> None:
    assert parse_cors_origins("https://a.invalid,,  ,https://b.invalid,") == [
        "https://a.invalid",
        "https://b.invalid",
    ]


def test_duplicates_are_collapsed() -> None:
    assert parse_cors_origins("https://a.invalid, https://a.invalid") == ["https://a.invalid"]


def test_an_explicit_wildcard_is_passed_through_but_is_never_the_default() -> None:
    # Parsing does not editorialise: if an operator writes `*` they get `*`.
    # What matters is that they had to write it -- see the unset/empty cases.
    assert parse_cors_origins("*") == ["*"]


def test_the_variable_name_is_the_documented_one() -> None:
    assert CORS_ALLOWED_ORIGINS_ENV == "CORS_ALLOWED_ORIGINS"


# --------------------------------------------------------------------------- #
# The wired middleware
# --------------------------------------------------------------------------- #


def _cors_options(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> dict[str, Any]:
    """Build the app under a given env value and return CORSMiddleware's kwargs."""
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    if raw is None:
        monkeypatch.delenv(CORS_ALLOWED_ORIGINS_ENV, raising=False)
    else:
        monkeypatch.setenv(CORS_ALLOWED_ORIGINS_ENV, raw)
    get_settings.cache_clear()
    app = create_app()
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return dict(middleware.kwargs)
    raise AssertionError("CORSMiddleware is not installed on the application")


def test_cors_middleware_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cors_options(monkeypatch, ALLOWED_ORIGIN)["allow_origins"] == [ALLOWED_ORIGIN]


def test_unset_environment_installs_an_empty_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cors_options(monkeypatch, None)["allow_origins"] == []


def test_credentials_are_disabled_so_wildcard_plus_credentials_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _cors_options(monkeypatch, "*")
    assert options["allow_credentials"] is False


def test_methods_and_headers_are_restricted_not_wildcards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _cors_options(monkeypatch, ALLOWED_ORIGIN)
    assert options["allow_methods"] == ["GET", "POST", "DELETE"] == list(CORS_ALLOWED_METHODS)
    assert options["allow_headers"] == ["Authorization", "Content-Type"] == list(
        CORS_ALLOWED_HEADERS
    )
    assert "*" not in options["allow_methods"]
    assert "*" not in options["allow_headers"]


def test_request_id_and_retry_after_are_exposed_to_browser_js(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _cors_options(monkeypatch, ALLOWED_ORIGIN)
    assert options["expose_headers"] == ["X-Request-Id", "Retry-After"] == list(
        CORS_EXPOSED_HEADERS
    )


def test_cors_middleware_is_outermost_so_error_responses_carry_the_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(CORS_ALLOWED_ORIGINS_ENV, ALLOWED_ORIGIN)
    get_settings.cache_clear()
    app = create_app()
    # Starlette builds the stack with user_middleware[0] outermost.
    assert app.user_middleware[0].cls is CORSMiddleware


# --------------------------------------------------------------------------- #
# End to end through the ASGI stack
# --------------------------------------------------------------------------- #


def _client(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> TestClient:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    if raw is None:
        monkeypatch.delenv(CORS_ALLOWED_ORIGINS_ENV, raising=False)
    else:
        monkeypatch.setenv(CORS_ALLOWED_ORIGINS_ENV, raw)
    get_settings.cache_clear()
    # No `with`: the lifespan would build real Supabase/HTTP clients. CORS is
    # middleware, so it runs without the lifespan having started.
    return TestClient(create_app(), raise_server_exceptions=False)


def test_preflight_from_an_allowed_origin_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, ALLOWED_ORIGIN).options(
        "/v1/uploads/ticket",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


def test_preflight_from_an_unlisted_origin_is_not_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch, ALLOWED_ORIGIN).options(
        "/v1/uploads/ticket",
        headers={
            "Origin": "https://evil.invalid",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_with_the_variable_unset_no_origin_is_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, None).options(
        "/v1/uploads/ticket",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_an_error_response_still_carries_the_allow_origin_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error must be readable by the browser, not an opaque network failure.

    A 404 on an unrouted path is used rather than a real endpoint because those
    need the lifespan-built `app.state` objects; the middleware and the
    exception handlers -- the two things under test -- do not.
    """
    response = _client(monkeypatch, ALLOWED_ORIGIN).get(
        "/v1/not-a-route", headers={"Origin": ALLOWED_ORIGIN}
    )
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-expose-headers"] == "X-Request-Id, Retry-After"
    # The flat envelope, not FastAPI's {"detail": ...}.
    assert set(response.json()) == {"error_code", "message", "retryable", "request_id"}
