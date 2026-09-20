"""Stage 1 — `POST /v1/uploads/ticket`.

The path is chosen by the SERVER, never proposed by the client:
`swing-videos/{user_id}/{uuid4}.{ext}`. That `user_id` prefix is the whole
reason the Stage 3 ownership check is a string comparison rather than a
database round trip.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from app.api.auth import AuthedUser, get_current_user
from app.api.errors import ApiError, openapi_errors
from app.config import Settings, get_settings
from app.models.enums import ErrorCode
from app.models.requests import ALLOWED_CONTENT_TYPES, UploadTicketRequest, UploadTicketResponse
from app.services.storage import StorageClient, get_storage_client

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])


@router.post(
    "/ticket",
    response_model=UploadTicketResponse,
    status_code=status.HTTP_200_OK,
    responses=openapi_errors(
        ErrorCode.INVALID_REQUEST,
        ErrorCode.UNSUPPORTED_CONTENT_TYPE,
        ErrorCode.AUTH_INVALID_TOKEN,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.STORAGE_UNAVAILABLE,
    ),
    summary="Issue a signed Supabase Storage upload destination.",
)
async def create_upload_ticket(
    body: UploadTicketRequest,
    user: AuthedUser = Depends(get_current_user),
    storage: StorageClient = Depends(get_storage_client),
    settings: Settings = Depends(get_settings),
) -> UploadTicketResponse:
    extension = ALLOWED_CONTENT_TYPES.get(body.content_type)
    if extension is None:
        # Checked here rather than as a Pydantic `pattern=` so the failure is
        # the specific code Stage 1 promises, not a generic invalid_request.
        raise ApiError(ErrorCode.UNSUPPORTED_CONTENT_TYPE)

    if body.size_bytes > settings.max_upload_bytes:
        # Same reason: Stage 1 promises 413 file_too_large for a declared size
        # over the cap. `settings.max_upload_bytes` is the single source, and it
        # matches Supabase's per-file limit -- a larger API cap would be accepted
        # here and then rejected by Storage, after the upload.
        raise ApiError(ErrorCode.FILE_TOO_LARGE)

    storage_path = f"{settings.supabase_storage_bucket}/{user.user_id}/{uuid.uuid4()}.{extension}"
    signed = await storage.create_signed_upload_url(storage_path)
    return UploadTicketResponse(
        storage_path=storage_path,
        upload_url=signed.upload_url,
        upload_token=signed.upload_token,
        # The current client does not read expires_at; it is sent anyway because
        # PIPELINE.md §2.2 specifies it and it is the only way a client can ever
        # distinguish an expired ticket from a broken one.
        expires_at=signed.expires_at,
    )
