"""Match mode routes (2026-11-09).

* ``POST   /v1/matches``        -> 202, the queued record
* ``GET    /v1/matches``        -> 200, the user's matches, newest first
* ``GET    /v1/matches/{id}``   -> 200, the record (status, progress, result)
* ``GET    /v1/matches/{id}/court.jpg`` -> the court lines that were used
* ``DELETE /v1/matches/{id}``   -> 204

The video is uploaded first through ``POST /v1/uploads/ticket`` with
``purpose: "match"``; it is deleted once the match has been processed.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.auth import AuthedUser, get_current_user
from app.api.errors import ApiError, openapi_errors
from app.models.enums import ErrorCode
from app.models.match import CreateMatchRequest, MatchListResponse, MatchRecord, SessionKind
from app.services.match_jobs import MatchJobs, get_match_jobs

router = APIRouter(prefix="/v1/matches", tags=["matches"])


@router.post(
    "",
    response_model=MatchRecord,
    status_code=status.HTTP_202_ACCEPTED,
    responses=openapi_errors(
        ErrorCode.INVALID_REQUEST,
        ErrorCode.AUTH_INVALID_TOKEN,
        ErrorCode.STORAGE_PATH_FORBIDDEN,
        ErrorCode.OBJECT_NOT_FOUND,
        ErrorCode.QUEUE_FULL,
        ErrorCode.STORAGE_UNAVAILABLE,
    ),
    summary="Queue a match video for shot, rally and line-call analysis.",
)
async def create_match(
    body: CreateMatchRequest,
    user: AuthedUser = Depends(get_current_user),
    jobs: MatchJobs = Depends(get_match_jobs),
) -> MatchRecord:
    record, _future = await jobs.create(user.user_id, body)
    return record


@router.get(
    "",
    response_model=MatchListResponse,
    responses=openapi_errors(ErrorCode.AUTH_INVALID_TOKEN, ErrorCode.STORAGE_UNAVAILABLE),
    summary="The user's matches (or rallies, with ?kind=rally), newest first.",
)
async def list_matches(
    kind: SessionKind | None = Query(default=None),
    user: AuthedUser = Depends(get_current_user),
    jobs: MatchJobs = Depends(get_match_jobs),
) -> MatchListResponse:
    return MatchListResponse(items=await jobs.list(user.user_id, kind))


@router.get(
    "/{match_id}",
    response_model=MatchRecord,
    responses=openapi_errors(
        ErrorCode.AUTH_INVALID_TOKEN, ErrorCode.ANALYSIS_NOT_FOUND, ErrorCode.STORAGE_UNAVAILABLE
    ),
    summary="One match: its status while it runs, its shot map when complete.",
)
async def get_match(
    match_id: UUID,
    user: AuthedUser = Depends(get_current_user),
    jobs: MatchJobs = Depends(get_match_jobs),
) -> MatchRecord:
    record = await jobs.get(user.user_id, match_id)
    if record is None:
        raise ApiError(ErrorCode.ANALYSIS_NOT_FOUND)
    return record


@router.get(
    "/{match_id}/court.jpg",
    responses=openapi_errors(
        ErrorCode.AUTH_INVALID_TOKEN, ErrorCode.ANALYSIS_NOT_FOUND, ErrorCode.STORAGE_UNAVAILABLE
    ),
    response_class=Response,
    summary="The frame the court was found on, with the lines that were used.",
)
async def get_court_picture(
    match_id: UUID,
    user: AuthedUser = Depends(get_current_user),
    jobs: MatchJobs = Depends(get_match_jobs),
) -> Response:
    picture = await jobs.court_picture(user.user_id, match_id)
    if picture is None:
        raise ApiError(ErrorCode.ANALYSIS_NOT_FOUND)
    return Response(content=picture, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.delete(
    "/{match_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=openapi_errors(
        ErrorCode.AUTH_INVALID_TOKEN, ErrorCode.ANALYSIS_NOT_FOUND, ErrorCode.STORAGE_UNAVAILABLE
    ),
    summary="Delete one match.",
)
async def delete_match(
    match_id: UUID,
    user: AuthedUser = Depends(get_current_user),
    jobs: MatchJobs = Depends(get_match_jobs),
) -> Response:
    if not await jobs.delete(user.user_id, match_id):
        raise ApiError(ErrorCode.ANALYSIS_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
