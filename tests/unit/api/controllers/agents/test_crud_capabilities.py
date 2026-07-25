"""Tests for the agent CRUD handlers' model-capability projection.

The capability projection is derived display data layered onto handlers
that have already committed a write, so its failure mode is the point of
interest: a settings-store outage must degrade the projection rather than
turn a successful mutation into a 500 the client would retry.
"""

from typing import Final
from unittest.mock import AsyncMock

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.agents.crud import AgentCrudController
from synthorg.api.cursor import CursorSecret
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.enums import AuthType
from synthorg.settings.errors import SettingsError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_PROVIDER: Final[str] = "test-provider"
_MODEL: Final[str] = "test-large-001"


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
