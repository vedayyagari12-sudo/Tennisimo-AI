"""Supabase Storage access (Stage 1 ticket issuance, Stage 3 HEAD, Stage 4 fetch).

DEPENDENCY DECISION, recorded per DATABASE_SETUP.md Part 5.2 item 3: this module
uses **httpx directly and not `supabase-py`**. The backend makes exactly three
Storage calls -- create a signed upload URL, HEAD an object, stream an object to
disk -- all plain HTTPS. The SDK's value here is convenience; its cost is a
dependency tree sitting over the single most security-sensitive credential in
the system (the service-role key). `supabase-py` is also not installed in this
environment, so adopting it would have meant a new dependency for three requests.

The service-role key BYPASSES RLS (Part 2.1). Ownership is therefore enforced by
the caller -- the Stage 3 prefix check in `routes_analyses.py` -- before any
method here is invoked with a user-supplied path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import Request

from app.api.errors import ApiError
from app.config import Settings
from app.models.enums import ErrorCode

logger = logging.getLogger("tennisform.storage")

#: Stage 4: stream to disk in 1 MB chunks. NEVER `.read()` the whole object --
#: a 50 MB bytes object alongside a loaded MediaPipe graph is the most likely
#: OOM in this system.
DOWNLOAD_CHUNK_BYTES: Final[int] = 1024 * 1024


@dataclass(frozen=True)
class SignedUpload:
    """Stage 1 result: where the phone uploads and with what token."""

    upload_url: str
    upload_token: str
    expires_at: datetime


@dataclass(frozen=True)
class ObjectHead:
    """Stage 3 existence + size probe."""

    size_bytes: int | None
    content_type: str | None


class StorageClient(Protocol):
    """The Storage surface the API layer depends on.

    A Protocol so the integration tests can inject a fake: no test in this repo
    makes a network call or touches a real video.
    """

    async def create_signed_upload_url(self, storage_path: str) -> SignedUpload: ...

    async def head_object(self, storage_path: str) -> ObjectHead: ...

    async def download_to_path(self, storage_path: str, destination: Path) -> int: ...


def split_bucket_path(storage_path: str) -> tuple[str, str]:
    """``"swing-videos/uid/file.mp4"`` -> ``("swing-videos", "uid/file.mp4")``."""
    bucket, _, key = storage_path.partition("/")
    return bucket, key


class SupabaseStorageClient:
    """Concrete `StorageClient` over the Supabase Storage REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._base = f"{settings.supabase_url}/storage/v1"
        key = settings.supabase_service_role_key.get_secret_value()
        # Both headers are required by Supabase: `apikey` routes the request,
        # `Authorization` authorizes it. The value never appears in a log line.
        self._headers: dict[str, str] = {"apikey": key, "Authorization": f"Bearer {key}"}

    async def create_signed_upload_url(self, storage_path: str) -> SignedUpload:
        bucket, key = split_bucket_path(storage_path)
        url = f"{self._base}/object/upload/sign/{bucket}/{key}"
        try:
            response = await self._client.post(url, headers=self._headers, json={})
        except httpx.HTTPError as exc:
            logger.warning("storage signed-url request failed: %s", type(exc).__name__)
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE) from exc

        if response.status_code >= 400:
            logger.warning("storage signed-url returned %s", response.status_code)
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE)

        try:
            signed_path = str(response.json()["url"])
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("storage signed-url body was not understood")
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE) from exc

        token_values = parse_qs(urlsplit(signed_path).query).get("token", [])
        if not token_values:
            logger.warning("storage signed-url carried no token")
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE)

        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.signed_upload_url_ttl_s)
        return SignedUpload(
            upload_url=f"{self._base}{signed_path}" if signed_path.startswith("/") else signed_path,
            upload_token=token_values[0],
            expires_at=expires_at,
        )

    async def head_object(self, storage_path: str) -> ObjectHead:
        bucket, key = split_bucket_path(storage_path)
        url = f"{self._base}/object/{bucket}/{key}"
        try:
            response = await self._client.head(url, headers=self._headers)
        except httpx.HTTPError as exc:
            logger.warning("storage HEAD failed: %s", type(exc).__name__)
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE) from exc

        if response.status_code in (404, 400):
            raise ApiError(ErrorCode.OBJECT_NOT_FOUND)
        if response.status_code >= 400:
            logger.warning("storage HEAD returned %s", response.status_code)
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE)

        raw_length = response.headers.get("content-length")
        size: int | None
        try:
            # None, not 0, when the header is absent: an unknown size is not a
            # measured zero, and the caller must be able to tell them apart.
            size = int(raw_length) if raw_length is not None else None
        except ValueError:
            size = None
        return ObjectHead(size_bytes=size, content_type=response.headers.get("content-type"))

    async def download_to_path(self, storage_path: str, destination: Path) -> int:
        """Stage 4. Stream to disk in 1 MB chunks; return the bytes written."""
        bucket, key = split_bucket_path(storage_path)
        url = f"{self._base}/object/{bucket}/{key}"
        written = 0
        try:
            async with self._client.stream("GET", url, headers=self._headers) as response:
                if response.status_code in (404, 400):
                    raise ApiError(ErrorCode.OBJECT_NOT_FOUND)
                if response.status_code >= 400:
                    raise ApiError(ErrorCode.STORAGE_UNAVAILABLE)
                with destination.open("wb") as handle:
                    async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                        written += len(chunk)
                        if written > self._settings.max_upload_bytes:
                            raise ApiError(ErrorCode.FILE_TOO_LARGE)
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            logger.warning("storage download failed: %s", type(exc).__name__)
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE) from exc
        return written


def get_storage_client(request: Request) -> StorageClient:
    """Dependency returning the startup-built client from ``app.state``."""
    client: StorageClient | None = getattr(request.app.state, "storage_client", None)
    if client is None:  # pragma: no cover - misconfigured app
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    return client
