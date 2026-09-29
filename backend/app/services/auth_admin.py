"""Supabase Auth (GoTrue) Admin API: account deletion only.

httpx, not `supabase-py` -- see the dependency decision in `storage.py`.

HARD DELETE, deliberately and explicitly. GoTrue's
``DELETE /auth/v1/admin/users/{id}`` takes an optional JSON body
``{"should_soft_delete": bool}``. With ``true`` the handler calls
``SoftDeleteUser`` / ``SoftDeleteUserIdentities`` -- the row survives with
``deleted_at`` set, so the old identity lingers. With
``false`` (or no body) the handler runs ``tx.Destroy(user)``: the
``auth.users`` row is removed, so a later sign-up with the same email is a
brand-new account and gets a fresh confirmation email. The default is already
``false`` (supabase/auth ``internal/api/admin.go`` `adminUserDelete`), but it is
SENT explicitly so this code never depends on a server-side default; the
official `supabase_auth` client's `delete_user` does the same.
"""

from __future__ import annotations

import logging
from typing import Final, Protocol
from uuid import UUID

import httpx
from fastapi import Request

from app.api.errors import ApiError
from app.config import Settings
from app.models.enums import ErrorCode

logger = logging.getLogger("tennisform.auth_admin")

#: The body that makes the delete a HARD delete. Module-level so the test can
#: assert the exact wire value.
HARD_DELETE_BODY: Final[dict[str, bool]] = {"should_soft_delete": False}


class AuthAdminClient(Protocol):
    """The Auth-admin surface the API layer depends on. A Protocol so tests fake it."""

    async def delete_user(self, user_id: UUID) -> None: ...


class SupabaseAuthAdminClient:
    """Concrete `AuthAdminClient` over the GoTrue Admin REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._client = client
        self._base = f"{settings.supabase_url}/auth/v1"
        key = settings.supabase_service_role_key.get_secret_value()
        # Same pair as storage.py / repository.py. The value never appears in a log line.
        self._headers: dict[str, str] = {"apikey": key, "Authorization": f"Bearer {key}"}

    async def delete_user(self, user_id: UUID) -> None:
        """Hard-delete one auth user. A user that is already gone counts as success.

        404 is GoTrue's answer for an unknown id (`loadUser` -> ``user_not_found``).
        It is swallowed so that a retry of `DELETE /v1/account` after this step
        already succeeded completes cleanly instead of failing forever.
        """
        try:
            response = await self._client.request(
                "DELETE",
                f"{self._base}/admin/users/{user_id}",
                headers=self._headers,
                json=HARD_DELETE_BODY,
            )
        except httpx.HTTPError as exc:
            logger.warning("auth admin delete failed: %s", type(exc).__name__)
            raise ApiError(ErrorCode.INTERNAL_ERROR) from exc
        if response.status_code == 404:
            logger.info("auth admin delete: user already absent")
            return
        if response.status_code >= 400:
            logger.warning("auth admin delete returned %s", response.status_code)
            raise ApiError(ErrorCode.INTERNAL_ERROR)


def get_auth_admin_client(request: Request) -> AuthAdminClient:
    """Dependency returning the startup-built client from ``app.state``."""
    client: AuthAdminClient | None = getattr(request.app.state, "auth_admin_client", None)
    if client is None:  # pragma: no cover - misconfigured app
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    return client
