"""Stage 2 — Supabase JWT verification. Applies to every route.

Fixed verification parameters, per PIPELINE.md Stage 2 and CLAUDE.md:

* ``algorithms=["ES256"]`` -- ES256 ONLY. Legacy HS256 shared-secret tokens are
  rejected; accepting both would mean anyone holding the anon key could forge a
  token. If the Supabase project has not been migrated to asymmetric keys, that
  migration is a prerequisite, not a fallback.
* ``audience="authenticated"``
* ``issuer=f"{SUPABASE_URL}/auth/v1"``
* ``options={"require": ["exp", "sub", "aud", "iss"]}``
* ``leeway=10`` seconds
* ``PyJWKClient(..., cache_keys=True, lifespan=300)`` constructed ONCE at
  application startup and held on ``app.state`` -- constructing it per request
  causes a JWKS fetch per request and will rate-limit under load.

Every failure -- missing header, malformed header, expired, bad signature,
wrong ``aud``, wrong ``iss``, unknown ``kid`` -- is ``401 auth_invalid_token``
with no further detail. Distinguishing them for the caller would be an oracle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from uuid import UUID

import jwt
from fastapi import Depends, Request
from jwt import PyJWKClient

from app.api.errors import ApiError
from app.config import Settings
from app.models.enums import ErrorCode

logger = logging.getLogger("tennisform.auth")

JWT_ALGORITHMS: Final[list[str]] = ["ES256"]
JWT_REQUIRED_CLAIMS: Final[list[str]] = ["exp", "sub", "aud", "iss"]
JWT_LEEWAY_S: Final[int] = 10
JWKS_CACHE_LIFESPAN_S: Final[int] = 300
BEARER_PREFIX: Final[str] = "bearer "


@dataclass(frozen=True)
class AuthedUser:
    """Stage 2 output. ``sub`` becomes ``user_id``."""

    user_id: UUID
    email: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


class TokenVerifier(Protocol):
    """Anything that can turn a bearer token into an ``AuthedUser``.

    A Protocol so tests can substitute an offline double: no test in this repo
    may make a network call, and a real ``PyJWKClient`` fetches JWKS.
    """

    def verify(self, token: str) -> AuthedUser:
        """Return the authenticated user, or raise ``ApiError(AUTH_INVALID_TOKEN)``."""
        ...


class SupabaseJwtVerifier:
    """The real verifier. Built once at startup; never per request."""

    def __init__(self, settings: Settings) -> None:
        self._audience: str = settings.supabase_jwt_audience
        self._issuer: str = settings.supabase_jwt_issuer
        # cache_keys=True + lifespan: one JWKS fetch per 5 minutes, not per request.
        self._jwk_client: PyJWKClient = PyJWKClient(
            settings.supabase_jwks_url,
            cache_keys=True,
            lifespan=JWKS_CACHE_LIFESPAN_S,
        )

    @property
    def jwk_client(self) -> PyJWKClient:
        return self._jwk_client

    def verify(self, token: str) -> AuthedUser:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=JWT_ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": JWT_REQUIRED_CLAIMS},
                leeway=JWT_LEEWAY_S,
            )
        except Exception as exc:  # noqa: BLE001 - every cause is the same 401
            # Logged at debug: the reason helps an operator and must not reach
            # the client, where it would be an oracle.
            logger.debug("jwt verification failed: %s", exc)
            raise ApiError(ErrorCode.AUTH_INVALID_TOKEN) from exc

        subject = claims.get("sub")
        if not isinstance(subject, str):
            raise ApiError(ErrorCode.AUTH_INVALID_TOKEN)
        try:
            user_id = UUID(subject)
        except ValueError as exc:
            raise ApiError(ErrorCode.AUTH_INVALID_TOKEN) from exc

        email = claims.get("email")
        return AuthedUser(
            user_id=user_id,
            email=email if isinstance(email, str) else None,
            raw_claims=claims,
        )


def get_token_verifier(request: Request) -> TokenVerifier:
    """Dependency returning the startup-built verifier from ``app.state``.

    Overridable in tests via ``app.dependency_overrides`` so the header parsing
    and the 401 mapping below stay under test with no network.
    """
    verifier: TokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:  # pragma: no cover - misconfigured app
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    return verifier


def get_current_user(
    request: Request,
    verifier: TokenVerifier = Depends(get_token_verifier),
) -> AuthedUser:
    """FastAPI dependency: ``Authorization: Bearer <supabase_jwt>`` -> ``AuthedUser``."""
    header = request.headers.get("authorization")
    if not header:
        raise ApiError(ErrorCode.AUTH_INVALID_TOKEN)
    if not header.lower().startswith(BEARER_PREFIX):
        raise ApiError(ErrorCode.AUTH_INVALID_TOKEN)
    token = header[len(BEARER_PREFIX) :].strip()
    if not token:
        raise ApiError(ErrorCode.AUTH_INVALID_TOKEN)
    return verifier.verify(token)
