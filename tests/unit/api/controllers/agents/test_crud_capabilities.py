"""Tests for the agent CRUD handlers' model-capability projection.

The capability projection is derived display data layered onto handlers
that have already committed a write, so its failure mode is the point of
interest: a settings-store outage must degrade the projection rather than
turn a successful mutation into a 500 the client would retry.
"""

from types import ModuleType
from typing import Final
from unittest.mock import AsyncMock

import pytest
from litestar.datastructures import State
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR

from synthorg.api.controllers.agents import crud as agent_crud
from synthorg.api.controllers.agents.crud import AgentCrudController
from synthorg.api.controllers.departments import crud as department_crud
from synthorg.api.cursor import CursorSecret
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.enums import AuthType
from synthorg.settings.errors import SettingsError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import LoopAsyncClient, make_app_state, mock_of

pytestmark = pytest.mark.unit

_PROVIDER: Final[str] = "test-provider"
_MODEL: Final[str] = "test-expert-001"


def _controller() -> AgentCrudController:
    """Build a route-free controller instance for direct handler calls.

    Returns:
        An uninitialised controller usable with the ``.fn`` handler form.
    """
    return object.__new__(AgentCrudController)


def _agent(name: str = "Ada") -> AgentConfig:
    """Build an agent bound to the test provider's model.

    Returns:
        The agent config.
    """
    return AgentConfig(
        name=name,
        role="Engineer",
        department="engineering",
        model={"provider": _PROVIDER, "model_id": _MODEL},
    )


def _providers() -> dict[str, ProviderConfig]:
    """Build a provider mapping carrying capability metadata.

    Returns:
        Provider mapping keyed by the test provider name.
    """
    return {
        _PROVIDER: ProviderConfig(
            auth_type=AuthType.NONE,
            models=(
                ProviderModelConfig(
                    id=_MODEL,
                    metadata=ModelMetadata(
                        supports_tools=True,
                        supports_reasoning=True,
                        supports_vision=False,
                        tool_calls_verified=True,
                        metadata_source="probe",
                    ),
                ),
            ),
        )
    }


def _state(resolver: object) -> State:
    """Wrap *resolver* in the app state the CRUD handlers read.

    Returns:
        A ``State`` carrying the composed app state.
    """
    state = State()
    state.app_state = make_app_state(
        config_resolver=resolver, cursor_secret=CursorSecret.ephemeral()
    )
    return state


class TestListAgentsCapabilities:
    async def test_projects_capabilities_onto_the_page(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_agents=AsyncMock(return_value=(_agent(),)),
            get_provider_configs=AsyncMock(return_value=_providers()),
        )

        result = await AgentCrudController.list_agents.fn(
            _controller(), state=_state(resolver)
        )

        (listed,) = result.data
        assert listed.model_capabilities is not None
        assert listed.model_capabilities.tool_calling == "verified"
        assert listed.model_capabilities.supports_reasoning is True

    async def test_degrades_when_provider_config_is_unreadable(self) -> None:
        # The roster must still render when the settings store cannot serve
        # provider config: capabilities are derived data, the agents are not.
        resolver = mock_of[ConfigResolver](
            get_agents=AsyncMock(return_value=(_agent(),)),
            get_provider_configs=AsyncMock(side_effect=SettingsError()),
        )

        result = await AgentCrudController.list_agents.fn(
            _controller(), state=_state(resolver)
        )

        (listed,) = result.data
        assert listed.name == "Ada"
        assert listed.model_capabilities is None
        # Reported as an outage, never as a broken binding: the dashboard
        # branches on this to avoid accusing every agent of a stale model.
        assert listed.model_capability_status == "provider_config_unavailable"


class TestGetAgentCapabilities:
    async def test_degrades_when_provider_config_is_unreadable(self) -> None:
        agent = _agent()
        resolver = mock_of[ConfigResolver](
            get_agents=AsyncMock(return_value=(agent,)),
            get_provider_configs=AsyncMock(side_effect=SettingsError()),
        )

        result = await AgentCrudController.get_agent.fn(
            _controller(), state=_state(resolver), agent_id=str(agent.id)
        )

        assert result.data is not None
        assert result.data.model_capabilities is None
        assert result.data.model_capability_status == "provider_config_unavailable"


def _projection_failure(*_args: object, **_kwargs: object) -> tuple[object, ...]:
    """Stand in for a projection that fails after the write has committed.

    Raises:
        ValueError: Always, matching the ``ValidationError`` a response model
            raises when it rejects a field the persisted config allowed.
    """
    msg = "projection rejected the persisted config"
    raise ValueError(msg)


def _break_projection(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType
) -> list[tuple[object, ...]]:
    """Make *module*'s projection fail and capture whatever it announces.

    Args:
        monkeypatch: Patcher scoped to the calling test.
        module: Controller module whose mutation paths are under test.

    Returns:
        Published events, in publish order.
    """
    published: list[tuple[object, ...]] = []
    monkeypatch.setattr(module, "with_model_capabilities", _projection_failure)
    monkeypatch.setattr(
        module,
        "publish_ws_event",
        lambda *args, **_kwargs: published.append(args),
    )
    return published


@pytest.mark.unit
class TestProjectionPrecedesAnnouncement:
    """Capability-projecting mutations build the response before announcing.

    Publishing is fire-and-forget, so once the event is out subscribers have
    been told the mutation happened. Projecting afterwards would let a
    projection failure fail the response while the dashboard resyncs to a
    change its requester was shown as an error.
    """

    async def test_create_agent_does_not_announce(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await async_test_client.post("/api/v1/departments", json={"name": "eng"})
        published = _break_projection(monkeypatch, agent_crud)

        resp = await async_test_client.post(
            "/api/v1/agents",
            json={"name": "alice", "role": "dev", "department": "eng"},
        )

        assert resp.status_code >= HTTP_500_INTERNAL_SERVER_ERROR
        assert published == []

    async def test_update_agent_does_not_announce(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await async_test_client.post("/api/v1/departments", json={"name": "eng"})
        created = await async_test_client.post(
            "/api/v1/agents",
            json={"name": "alice", "role": "dev", "department": "eng"},
        )
        agent_id = created.json()["data"]["id"]
        published = _break_projection(monkeypatch, agent_crud)

        resp = await async_test_client.patch(
            f"/api/v1/agents/{agent_id}",
            json={"role": "lead"},
        )

        assert resp.status_code >= HTTP_500_INTERNAL_SERVER_ERROR
        assert published == []

    async def test_reorder_agents_does_not_announce(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await async_test_client.post("/api/v1/departments", json={"name": "eng"})
        for name in ("alice", "bob"):
            await async_test_client.post(
                "/api/v1/agents",
                json={"name": name, "role": "dev", "department": "eng"},
            )
        published = _break_projection(monkeypatch, department_crud)

        resp = await async_test_client.post(
            "/api/v1/departments/eng/reorder-agents",
            json={"agent_names": ["bob", "alice"]},
        )

        assert resp.status_code >= HTTP_500_INTERNAL_SERVER_ERROR
        assert published == []
