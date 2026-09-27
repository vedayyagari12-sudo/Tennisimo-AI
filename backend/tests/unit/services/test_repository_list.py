"""`SupabaseRepository.list_analyses` must SELECT `pipeline_version`.

The PostgREST query uses an explicit column list, so a field added to
`AnalysisListRow` but not to the `select=` would fail validation on every live
row. Driven through `httpx.MockTransport`: no network.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx

from app.config import Settings
from app.services.repository import AnalysisListRow, SupabaseRepository

FAKE_ENV = {
    "SUPABASE_URL": "https://fake-project.supabase.invalid",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key-not-a-secret",
    "GEMINI_API_KEY": "fake-gemini-key-not-a-secret",
}
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
ROW_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_list_query_selects_pipeline_version_and_row_carries_it() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": str(ROW_ID),
                    "created_at": "2026-09-20T10:00:00+00:00",
                    "shot_type": "forehand_topspin",
                    "overall_score": 71.5,
                    "ball_speed_mph": None,
                    "pipeline_version": "v2",
                }
            ],
        )

    async def run() -> list[AnalysisListRow]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repo = SupabaseRepository(Settings(env=FAKE_ENV), client)
            return await repo.list_analyses(USER_ID, 3, None)

    rows = asyncio.run(run())

    assert len(seen) == 1
    select = parse_qs(urlsplit(str(seen[0].url)).query)["select"][0]
    assert "pipeline_version" in select.split(",")
    assert rows[0].pipeline_version == "v2"
