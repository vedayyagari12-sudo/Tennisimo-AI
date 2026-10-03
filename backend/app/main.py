"""The ASGI application.

Deployed as `backend.app.main:app` (PIPELINE.md §1.20.1) with `--workers 1`:
a second uvicorn worker is a second process with its own ThreadPoolExecutor and
its own queue-depth counter inside one container, which is the exact defect
`--max-instances=1` exists to prevent.

Assembly order here is deliberate and matches DATABASE_SETUP.md Part 5.3 step 4:
**the middleware and the three exception handlers are installed BEFORE any
router is included.** Retrofitting an error envelope means auditing every
`raise` in the codebase.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api import routes_account, routes_analyses, routes_uploads
from app.api.auth import SupabaseJwtVerifier
from app.api.errors import RequestIdMiddleware, register_exception_handlers
from app.config import (
    CORS_ALLOWED_HEADERS,
    CORS_ALLOWED_METHODS,
    CORS_ALLOWED_ORIGINS_ENV,
    CORS_EXPOSED_HEADERS,
    get_settings,
    parse_cors_origins,
)
from app.services.auth_admin import SupabaseAuthAdminClient
from app.services.orchestrator import JobOrchestrator
from app.services.repository import SupabaseRepository
from app.services.storage import SupabaseStorageClient

logger = logging.getLogger("tennisform")


def _warm_pipeline() -> None:
    """Import the analysis pipeline so the first job does not pay for it.

    `default_job_runner` imports `app.services.pipeline` lazily, which pulls in
    MediaPipe, PyAV and OpenCV: 6-12 s of import on a fast desktop, more on a
    2 vCPU instance. Lazily, that cost landed on the FIRST job after every cold
    start -- after the user had already submitted. Running the same import on a
    background thread at startup moves it off that path: by the time a clip is
    uploaded and submitted it is usually done. If a job does arrive first, its
    own import simply waits on Python's import lock; nothing is imported twice.

    Best-effort by design. A failure here is logged and otherwise ignored: the
    job path still performs (and reports) the same import itself.
    """
    started = time.monotonic()
    try:
        importlib.import_module("app.services.pipeline")
    except Exception:  # noqa: BLE001 - warm-up must never take the server down
        logger.exception("pipeline warm-up failed; the first job will import it")
        return
    logger.info("pipeline warm-up done in %.1fs", time.monotonic() - started)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the long-lived, shared objects exactly once.

    `PyJWKClient` in particular MUST be constructed here and held on
    `app.state`: building it per request causes a JWKS fetch per request and
    will rate-limit under load (PIPELINE.md Stage 2).

    Settings are read here rather than at import time so that importing this
    module -- for a test, or for `--help` -- does not require the environment
    to be populated.
    """
    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.supabase_http_timeout_s)

    app.state.settings = settings
    app.state.http_client = http_client
    app.state.token_verifier = SupabaseJwtVerifier(settings)
    app.state.storage_client = SupabaseStorageClient(settings, http_client)
    app.state.repository = SupabaseRepository(settings, http_client)
    app.state.auth_admin_client = SupabaseAuthAdminClient(settings, http_client)
    app.state.orchestrator = JobOrchestrator(
        repository=app.state.repository,
        storage=app.state.storage_client,
        settings=settings,
        loop=asyncio.get_running_loop(),
    )
    if settings.warm_pipeline_on_startup:
        threading.Thread(target=_warm_pipeline, name="pipeline-warmup", daemon=True).start()
    logger.info("tennisform api started")
    try:
        yield
    finally:
        app.state.orchestrator.shutdown()
        await http_client.aclose()
        logger.info("tennisform api stopped")


def _openapi_without_default_422(app: FastAPI) -> dict[str, Any]:
    """Generated schema with FastAPI's automatic 422 removed.

    This service never returns 422: the ``RequestValidationError`` handler turns
    every Pydantic failure into ``400 invalid_request`` (Part 3.0). FastAPI adds
    a 422 / ``HTTPValidationError`` entry to every route that has a body or a
    query parameter regardless, and leaving it in advertises an error schema
    that can never be produced. The Flutter models are read off this schema by a
    human, and a wrong schema costs a day.
    """
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            operation.get("responses", {}).pop("422", None)
    schema.get("components", {}).get("schemas", {}).pop("HTTPValidationError", None)
    schema.get("components", {}).get("schemas", {}).pop("ValidationError", None)
    app.openapi_schema = schema
    return schema


def create_app() -> FastAPI:
    app = FastAPI(
        title="TennisForm AI",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.openapi = lambda: _openapi_without_default_422(app)  # type: ignore[method-assign]

    # 1. Correlation id on EVERY response, including 2xx.
    app.add_middleware(RequestIdMiddleware)

    # 2. CORS. Added AFTER RequestIdMiddleware on purpose: Starlette's
    #    `add_middleware` inserts at position 0 and the stack is built so that
    #    the LAST-added class ends up OUTERMOST. CORS must be outermost, or a
    #    preflight OPTIONS never reaches it and -- worse -- an error response
    #    produced by an inner layer goes back without the
    #    `Access-Control-Allow-Origin` header, which the browser turns into an
    #    opaque network failure instead of the flat error envelope the client
    #    knows how to render.
    app.add_middleware(
        CORSMiddleware,
        # Read from the raw environment rather than `get_settings()` so that
        # importing this module still does not require SUPABASE_URL et al. to be
        # populated (see `lifespan`). Empty list == no origin allowed: the
        # variable is not set, so nothing is permitted.
        allow_origins=parse_cors_origins(os.environ.get(CORS_ALLOWED_ORIGINS_ENV)),
        # FALSE, deliberately, and this is the decision the wildcard question
        # hangs on. Auth here is a Supabase access token that the client puts in
        # an `Authorization: Bearer ...` header (app/api/auth.py). That is an
        # ordinary request header, not an ambient credential: it is attached by
        # application code, not by the browser, so it crosses origins with
        # `credentials: "omit"` just fine. Nothing in this API reads a cookie,
        # sets a cookie, or uses TLS client certs -- the three things
        # `allow_credentials=True` actually exists for. Leaving it False means
        # the "`*` plus credentials" combination that browsers reject outright
        # is unreachable by construction, and it removes the CSRF surface that
        # credentialed CORS would add. If a cookie session is ever introduced,
        # this flips to True and `allow_origins` must stay explicit -- never
        # `["*"]`, and never `allow_origin_regex=".*"`, which is the same
        # mistake wearing a hat.
        allow_credentials=False,
        allow_methods=list(CORS_ALLOWED_METHODS),
        allow_headers=list(CORS_ALLOWED_HEADERS),
        expose_headers=list(CORS_EXPOSED_HEADERS),
    )

    # 3. The error envelope. Before any route, without exception.
    register_exception_handlers(app)

    # 4. Routes.
    app.include_router(routes_uploads.router)
    app.include_router(routes_analyses.router)
    app.include_router(routes_account.router)

    return app


app = create_app()
