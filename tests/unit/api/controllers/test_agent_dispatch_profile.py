"""Unit tests for the per-agent dispatch-comparison controller.

Calls the handlers directly with a fake ``State`` (the
``test_agent_roster`` pattern), plus one routing test through the real
Litestar router: the literal ``/agents/dispatch-profiles`` must not be
captured by the sibling ``/{agent_name:str}`` route, which is the failure
mode that makes a registered controller look absent.
"""

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.agents.dispatch_profile import (
    AgentDispatchProfileController,
)
from synthorg.api.cursor import CursorSecret
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.dispatch_profile import DEFAULT_MIN_CALLS_FOR_PROFILE
from tests._shared import LoopAsyncClient, make_app_state
from tests.unit.meta.chief_of_staff.propose_fakes import build_registry, make_identity

pytestmark = pytest.mark.unit


def _controller() -> AgentDispatchProfileController:
    """Build a route-free controller instance for direct handler calls."""
    return object.__new__(AgentDispatchProfileController)


async def test_every_active_agent_gets_a_profile() -> None:
    ceo = make_identity(name="Dana", role="CEO")
    cfo = make_identity(name="Casey", role="CFO")
    departed = make_identity(name="Old", role="COO", status=AgentStatus.TERMINATED)
    registry = await build_registry(ceo, cfo, departed)
    state = State()
    state.app_state = make_app_state(
        agent_registry=registry, cursor_secret=CursorSecret.ephemeral()
    )

    result = await AgentDispatchProfileController.list_dispatch_profiles.fn(
        _controller(), state=state
    )

    by_id = {row.agent_id: row for row in result.data}
    assert by_id.keys() == {str(ceo.id), str(cfo.id)}
    assert by_id[str(ceo.id)].role == "CEO"


async def test_an_agent_with_no_calls_reports_as_insufficient() -> None:
    # A new agent has made none, which is a true statement about it rather
    # than an absence to hide or a rate to invent.
    agent = make_identity(name="Dana", role="CEO")
    state = State()
    state.app_state = make_app_state(
        agent_registry=await build_registry(agent),
        cursor_secret=CursorSecret.ephemeral(),
    )

    result = await AgentDispatchProfileController.list_dispatch_profiles.fn(
        _controller(), state=state
    )

    profile = result.data[0]
    assert profile.call_count == 0
    assert not profile.has_enough_calls
    assert profile.min_calls == DEFAULT_MIN_CALLS_FOR_PROFILE


class TestDispatchProfileRouting:
    async def test_literal_route_resolves_to_the_comparison(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        # The agent is named "Dana", not "dispatch-profiles": were the
        # path-param route to win, this would 404.
        identity = make_identity(name="Dana", role="CEO")
        await agent_registry.register(identity)

        resp = await async_test_client.get("/api/v1/agents/dispatch-profiles")

        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert [row["agent_id"] for row in rows] == [str(identity.id)]
        assert rows[0]["role"] == "CEO"
        assert rows[0]["has_enough_calls"] is False

    async def test_per_agent_route_reports_that_agent(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        identity = make_identity(name="Dana", role="CEO")
        await agent_registry.register(identity)

        resp = await async_test_client.get(
            f"/api/v1/agents/{identity.id}/dispatch-profile"
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["agent_id"] == str(identity.id)
