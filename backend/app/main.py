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
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api import routes_analyses, routes_uploads
from app.api.auth import SupabaseJwtVerifier
from app.api.errors import RequestIdMiddleware, register_exception_handlers
from app.config import get_settings
from app.services.orchestrator import JobOrchestrator
from app.services.repository import SupabaseRepository
from app.services.storage import SupabaseStorageClient

logger = logging.getLogger("tennisform")


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
    app.state.orchestrator = JobOrchestrator(
        repository=app.state.repository,
        storage=app.state.storage_client,
        settings=settings,
        loop=asyncio.get_running_loop(),
    )
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

    # 2. The error envelope. Before any route, without exception.
    register_exception_handlers(app)

    # 3. Routes.
    app.include_router(routes_uploads.router)
    app.include_router(routes_analyses.router)

    return app


app = create_app()
