"""`DELETE /v1/account` -- permanently delete the caller's account and data.

The user deleted is always the caller (`jwt.sub`); there is no body and no way
to name another user.

ORDER, and why:

1. **Storage objects** under ``swing-videos/{user_id}/``.
2. **Rows**: `analyses`, then `analysis_jobs` -- explicitly, because the
   `user_id` cascade FKs are not confirmed live (DATABASE_SETUP.md Part 7).
3. **The auth user, LAST.** While it exists the person can still sign in and
   retry; once it is gone they cannot. So everything that can be retried runs
   before the one step that ends the ability to retry.

Every step is idempotent (empty folder -> no-op, zero-row DELETE -> 204, unknown
auth user -> 404 treated as done), so after a failure at any step a plain retry
of the whole request finishes the job. A failure surfaces as the usual error
envelope with ``retryable: true`` (503 `storage_unavailable` / 500
`internal_error`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status

from app.api.auth import AuthedUser, get_current_user
from app.api.errors import openapi_errors
from app.config import Settings, get_settings
from app.models.enums import ErrorCode
from app.services.auth_admin import AuthAdminClient, get_auth_admin_client
from app.services.repository import Repository, get_repository
from app.services.storage import StorageClient, get_storage_client

logger = logging.getLogger("tennisform.account")

router = APIRouter(prefix="/v1/account", tags=["account"])


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=openapi_errors(
        ErrorCode.AUTH_INVALID_TOKEN,
        ErrorCode.STORAGE_UNAVAILABLE,
        ErrorCode.INTERNAL_ERROR,
    ),
    summary="Permanently delete the caller's account, videos and analyses.",
)
async def delete_account(
    user: AuthedUser = Depends(get_current_user),
    storage: StorageClient = Depends(get_storage_client),
    repository: Repository = Depends(get_repository),
    auth_admin: AuthAdminClient = Depends(get_auth_admin_client),
    settings: Settings = Depends(get_settings),
) -> Response:
    # The folder is built from the VERIFIED token subject only -- never from
    # anything the client sent -- so it cannot reach another user's prefix.
    folder = f"{settings.supabase_storage_bucket}/{user.user_id}"
    removed = await storage.delete_folder(folder)
    await repository.delete_user_rows(user.user_id)
    await auth_admin.delete_user(user.user_id)
    logger.info("account deleted objects=%d", removed)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
