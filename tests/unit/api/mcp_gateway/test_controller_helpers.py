"""Unit tests for the credentialed-MCP controller's pure helpers.

The Request-taking body reader (``_read_messages``) is exercised in the
integration tier; here we cover the disabled-server guard, the capability
grant parsing, the deploy-settings resolution and the fail-closed actor
lookup, which decide whether the endpoint serves at all and what an actor
may see or do.
"""

from datetime import date
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from synthorg.api.mcp_gateway.controller import (
    _parse_capabilities,
    _parse_targets,
    _require_enabled,
    _resolve_actor,
    _resolve_deploy_settings,
)
from synthorg.api.state import AppState
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.role import Skill
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="deployer",
        role="developer",
        department="engineering",
        model=ModelConfig(provider="test-provider", model_id="test-medium-001"),
        personality=PersonalityConfig(
            traits=("detail-oriented",), communication_style="formal"
        ),
        skills=SkillSet(primary=(Skill(id="python", name="python"),), secondary=()),
        hiring_date=date(2026, 1, 1),
    )


class _Registry:
    """A minimal agent registry stub returning a preset identity."""

    def __init__(self, identity: AgentIdentity | None) -> None:
        self._identity = identity
        self.lookups: list[str] = []

    async def get(self, agent_id: str) -> AgentIdentity | None:
        self.lookups.append(str(agent_id))
        return self._identity


def _app_state(registry: object) -> AppState:
    state = mock_of[AppState]()
    state.slice.return_value = SimpleNamespace(agent_registry=registry)
    return cast("AppState", state)


def _resolver(
    *, enabled: bool, targets: str, timeout: float, max_log_chars: int
) -> ConfigResolver:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = enabled
    resolver.get_str.return_value = targets
    resolver.get_float.return_value = timeout
    resolver.get_int.return_value = max_log_chars
    return cast("ConfigResolver", resolver)


def test_require_enabled_raises_when_disabled() -> None:
    with pytest.raises(ServiceUnavailableError):
        _require_enabled(enabled=False)


def test_require_enabled_passes_when_enabled() -> None:
    _require_enabled(enabled=True)  # no raise


def test_parse_capabilities_splits_and_strips() -> None:
    assert _parse_capabilities("forge:read, chat:write ,connections:*") == (
        "forge:read",
        "chat:write",
        "connections:*",
    )


def test_parse_capabilities_drops_blank_entries() -> None:
    assert _parse_capabilities("forge:read,, ,chat:read") == (
        "forge:read",
        "chat:read",
    )


def test_parse_capabilities_empty_grant_is_empty_tuple() -> None:
    assert _parse_capabilities("") == ()
    assert _parse_capabilities("   ") == ()


def test_parse_targets_strips_and_drops_blanks() -> None:
    assert _parse_targets("prod, staging ,, ") == frozenset({"prod", "staging"})
    assert _parse_targets("") == frozenset()


async def test_resolve_actor_returns_none_without_a_registry() -> None:
    actor = await _resolve_actor(_app_state(None), agent_id="agent-1")
    assert actor is None


async def test_resolve_actor_returns_none_for_an_unknown_agent() -> None:
    registry = _Registry(None)
    actor = await _resolve_actor(_app_state(registry), agent_id="ghost")
    assert actor is None
    assert registry.lookups == ["ghost"]


async def test_resolve_actor_returns_the_registered_identity() -> None:
    identity = _identity()
    registry = _Registry(identity)
    actor = await _resolve_actor(_app_state(registry), agent_id="deployer")
    assert actor is identity


async def test_resolve_deploy_settings_parses_the_bundle() -> None:
    settings = await _resolve_deploy_settings(
        _resolver(
            enabled=True, targets="prod, staging", timeout=45.0, max_log_chars=10000
        )
    )
    assert settings.enabled is True
    assert settings.targets == frozenset({"prod", "staging"})
    assert settings.timeout_seconds == 45.0
    assert settings.max_log_chars == 10000


async def test_resolve_deploy_settings_carries_the_kill_switch_off() -> None:
    settings = await _resolve_deploy_settings(
        _resolver(enabled=False, targets="", timeout=30.0, max_log_chars=20000)
    )
    assert settings.enabled is False
    assert settings.targets == frozenset()
