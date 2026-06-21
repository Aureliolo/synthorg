"""The provider presets endpoint is cursor-paginated.

``GET /providers/presets`` returns a ``PaginatedResponse`` with a bounded
page and a cursor, so a growing preset catalogue cannot return an
unbounded page.
"""

import pytest

from tests._shared import LoopAsyncClient

pytestmark = pytest.mark.unit


class TestProviderPresetsPagination:
    async def test_presets_response_is_paginated(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/providers/presets")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        # The cursor-pagination envelope carries page metadata.
        assert "pagination" in body
        assert "has_more" in body["pagination"]

    async def test_presets_respects_limit(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/providers/presets?limit=2")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) <= 2
