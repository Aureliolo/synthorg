"""Tests for the ``GET /meta/config`` endpoint contract.

Locks the ``chief_of_staff.direct_mcp_ready`` effective-state field the
dashboard reads to cross-warn that direct-MCP acting is enabled but inert
(no security governance wired).
"""

import pytest

from synthorg.meta.state import MetaStateSlice
from tests._shared import LoopAsyncClient, mock_of
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_URL = "/api/v1/meta/config"
_HEADERS = make_auth_headers("ceo")


async def _get_cos_block(client: LoopAsyncClient) -> dict[str, object]:
    resp = await client.get(_URL, headers=_HEADERS)
    assert resp.status_code == 200
    cos = resp.json()["data"]["chief_of_staff"]
    assert isinstance(cos, dict)
    return cos


class TestMetaConfigDirectMcpReady:
    async def test_direct_mcp_ready_false_when_actor_unwired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        app_state.wire(MetaStateSlice, conversational_actor=None)

        cos = await _get_cos_block(async_test_client)

        assert cos["direct_mcp_ready"] is False

    async def test_direct_mcp_ready_true_when_actor_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        from synthorg.meta.chief_of_staff.actor import ConversationalActor

        app_state.wire(
            MetaStateSlice,
            conversational_actor=mock_of[ConversationalActor](),
        )

        cos = await _get_cos_block(async_test_client)

        assert cos["direct_mcp_ready"] is True
