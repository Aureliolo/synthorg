"""Unit tests for the runtime agent-roster controller.

Calls ``list_active_agents`` directly with a fake ``State`` (the
``test_budget_forecast_controller`` pattern) so the handler logic --
active-only filtering, UUID exposure, empty-org case, and the 503 when
the registry is unwired -- is covered without a full TestClient.
"""

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.agent_roster import (
    ActiveAgentSummary,
    AgentRosterController,
)
from synthorg.api.cursor import CursorSecret
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from tests._shared import LoopAsyncClient, make_app_state
from tests.unit.meta.chief_of_staff.propose_fakes import build_registry, make_identity

pytestmark = pytest.mark.unit


def _controller() -> AgentRosterController:
    """Build a route-free controller instance for direct handler calls."""
    return object.__new__(AgentRosterController)


async def test_lists_only_active_agents_with_uuids() -> None:
    ceo = make_identity(name="Dana", role="CEO")
    cfo = make_identity(name="Casey", role="CFO")
    departed = make_identity(name="Old", role="COO", status=AgentStatus.TERMINATED)
    registry = await build_registry(ceo, cfo, departed)
    state = State()
    state.app_state = make_app_state(
        agent_registry=registry, cursor_secret=CursorSecret.ephemeral()
    )

    result = await AgentRosterController.list_active_agents.fn(
        _controller(), state=state
    )

    summaries = result.data
    assert summaries is not None
    by_id = {s.id: s for s in summaries}
    # Only the two ACTIVE agents are returned, each carrying its UUID.
    assert by_id.keys() == {str(ceo.id), str(cfo.id)}
    assert str(departed.id) not in by_id
    assert by_id[str(ceo.id)].name == "Dana"
    assert by_id[str(ceo.id)].role == "CEO"
    assert all(isinstance(s, ActiveAgentSummary) for s in summaries)


async def test_empty_org_returns_empty_list() -> None:
    registry = await build_registry()
    state = State()
    state.app_state = make_app_state(
        agent_registry=registry, cursor_secret=CursorSecret.ephemeral()
    )

    result = await AgentRosterController.list_active_agents.fn(
        _controller(), state=state
    )

    assert result.data == ()


async def test_503_when_registry_unwired() -> None:
    state = State()
    state.app_state = make_app_state()
    with pytest.raises(ServiceUnavailableError):
        await AgentRosterController.list_active_agents.fn(_controller(), state=state)


@pytest.mark.unit
class TestAgentRosterRouting:
    """``GET /agents/active`` must resolve to the roster handler through the
    real Litestar router, not be captured by the sibling ``AgentController``
    ``/{agent_name:str}`` route as ``get_agent(agent_name="active")``.
    """

    async def test_active_route_resolves_to_roster_not_agent_detail(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        # The agent is named "Dana", not "active": were the path-param route
        # to win, the request would 404 (no agent named "active"). The roster
        # route instead returns this agent in a list with the roster shape.
        identity = make_identity(name="Dana", role="CEO")
        await agent_registry.register(identity)

        resp = await async_test_client.get("/api/v1/agents/active")

        assert resp.status_code == 200
        roster = resp.json()["data"]
        # Roster shape (exactly id/name/role), not the richer agent-detail
        # object the ``/{agent_name}`` route would have returned.
        assert roster == [{"id": str(identity.id), "name": "Dana", "role": "CEO"}]
