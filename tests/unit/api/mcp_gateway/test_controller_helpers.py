"""Unit tests for the credentialed-MCP controller's pure helpers.

The Request-taking body reader (``_read_messages``) is exercised in the
integration tier; here we cover the disabled-server guard, the capability
grant parsing, the deploy-settings resolution, the fail-closed actor
lookup, and the context assembly that carries the deploy allowlist and
kill switch to the dispatch layer: together these decide whether the
endpoint serves at all and what an actor may see or do.
"""

from datetime import date
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.mcp_gateway._request_context import (
    _build_context,
    _context_opener,
    _parse_targets,
    _resolve_actor,
    _resolve_autonomy,
    _resolve_deploy_settings,
    _resolve_kill_switches,
    _resolve_publish_settings,
)
from synthorg.api.mcp_gateway.controller import (
    _parse_capabilities,
    _require_enabled,
)
from synthorg.api.state import AppState
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.role import Skill
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.llm.gateway_token import GatewayTokenClaims
from synthorg.security.audit import AuditLog
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import config_resolver_of
from synthorg.workers.execution_service import AgentEngineExecutionService
from tests._shared import make_app_state, mock_of

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


def _autonomy_state(
    *,
    service: object,
    security_config: object = object(),
) -> AppState:
    """An app state whose runtime slice serves *service*.

    ``AppState.slice`` is keyed by slice class in production; the double
    returns one namespace carrying every attribute the autonomy path reads,
    which is enough because the path reads each by name.
    """
    state = mock_of[AppState]()
    state.slice.return_value = SimpleNamespace(
        agent_registry=None, worker_execution_service=service
    )
    state.security_runtime_config = SimpleNamespace(current=security_config)
    return cast("AppState", state)


class TestGatewayAutonomy:
    """C18: a credentialed gateway call is not a runless screen.

    The bearer names the agent and the task, which is exactly what autonomy
    is resolved from. Resolving nothing left every governed tool call reaching
    the gateway screened at the untiered fallback, whatever the operator had
    actually granted that agent.
    """

    async def test_a_runless_call_resolves_no_autonomy(self) -> None:
        """No task means no run to be governed by."""
        service = mock_of[AgentEngineExecutionService]()
        state = _autonomy_state(service=service)

        assert await _resolve_autonomy(state, _identity(), task_id=None) is None
        service.resolve_effective_autonomy.assert_not_awaited()

    async def test_an_unknown_actor_resolves_no_autonomy(self) -> None:
        """An agent the registry does not know cannot be tiered."""
        service = mock_of[AgentEngineExecutionService]()
        state = _autonomy_state(service=service)

        assert await _resolve_autonomy(state, None, task_id="task-1") is None
        service.resolve_effective_autonomy.assert_not_awaited()

    async def test_no_security_config_resolves_no_autonomy(self) -> None:
        """With no screen installed there is nothing to tier."""
        service = mock_of[AgentEngineExecutionService]()
        state = _autonomy_state(service=service, security_config=None)

        assert await _resolve_autonomy(state, _identity(), task_id="task-1") is None
        service.resolve_effective_autonomy.assert_not_awaited()

    async def test_a_foreign_execution_service_resolves_no_autonomy(self) -> None:
        """Only the agent-backed service owns the answer for other paths."""
        state = _autonomy_state(service=SimpleNamespace())

        assert await _resolve_autonomy(state, _identity(), task_id="task-1") is None

    async def test_a_governed_call_is_tiered_like_the_run_it_belongs_to(
        self,
    ) -> None:
        """Same service, same answer, so the gateway is not a second owner."""
        identity = _identity()
        expected = EffectiveAutonomy(
            level=AutonomyLevel.FULL,
            auto_approve_actions=frozenset({"comms:external"}),
            human_approval_actions=frozenset(),
            security_agent=False,
        )
        service = mock_of[AgentEngineExecutionService]()
        service.resolve_effective_autonomy.return_value = expected
        state = _autonomy_state(service=service)

        resolved = await _resolve_autonomy(state, identity, task_id="task-1")

        assert resolved is expected
        service.resolve_effective_autonomy.assert_awaited_once_with(
            identity, task_id="task-1"
        )


async def test_resolve_deploy_settings_parses_the_bundle() -> None:
    settings = await _resolve_deploy_settings(
        _resolver(
            enabled=True, targets="prod, staging", timeout=45.0, max_log_chars=10000
        )
    )
    assert settings.targets == frozenset({"prod", "staging"})
    assert settings.timeout_seconds == 45.0
    assert settings.max_log_chars == 10000


async def test_resolve_deploy_settings_parses_an_empty_allowlist() -> None:
    settings = await _resolve_deploy_settings(
        _resolver(enabled=False, targets="", timeout=30.0, max_log_chars=20000)
    )
    assert settings.targets == frozenset()


async def test_resolve_publish_settings_parses_the_bundle() -> None:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = True
    resolver.get_str.return_value = "prod-images"
    resolver.get_float.return_value = 60.0
    # Key the two byte caps distinctly so a cross-wired read would be caught.
    caps = {
        "publish_tools_max_manifest_bytes": 4_000_000,
        "publish_tools_max_image_bytes": 2_000_000_000,
    }
    resolver.get_int.side_effect = lambda _ns, key: caps[key]
    settings = await _resolve_publish_settings(cast("ConfigResolver", resolver))
    assert settings.targets == frozenset({"prod-images"})
    assert settings.timeout_seconds == 60.0
    assert settings.max_manifest_bytes == 4_000_000
    assert settings.max_image_bytes == 2_000_000_000


def _context_app_state(
    *,
    deploy_enabled: bool,
    publish_enabled: bool | None = None,
    targets: str = "prod",
    publish_targets: str = "prod-images",
    registry: object | None = None,
) -> AppState:
    resolver = mock_of[ConfigResolver]()
    # Every setting is keyed so the context build cannot silently source a
    # publish field from a deploy key (or vice versa) and pass unnoticed.
    bools = {
        "deploy_tools_enabled": deploy_enabled,
        "publish_tools_enabled": deploy_enabled
        if publish_enabled is None
        else publish_enabled,
    }
    strs = {
        "forge_tools_connection": "forge-conn",
        "chat_tools_connection": "chat-conn",
        "deploy_tools_targets": targets,
        "publish_tools_targets": publish_targets,
    }
    floats = {
        "forge_tools_timeout_seconds": 30.0,
        "chat_tools_timeout_seconds": 30.0,
        "deploy_tools_timeout_seconds": 45.0,
        "publish_tools_timeout_seconds": 60.0,
    }
    ints = {
        "forge_tools_max_read_chars": 5000,
        "deploy_tools_max_log_chars": 10000,
        "publish_tools_max_manifest_bytes": 4_000_000,
        "publish_tools_max_image_bytes": 2_000_000_000,
    }
    resolver.get_bool.side_effect = lambda _ns, key: bools[key]
    resolver.get_str.side_effect = lambda _ns, key: strs[key]
    resolver.get_float.side_effect = lambda _ns, key: floats[key]
    resolver.get_int.side_effect = lambda _ns, key: ints[key]
    return make_app_state(
        config_resolver=cast("ConfigResolver", resolver),
        connection_catalog=mock_of[ConnectionCatalog](),
        approval_store=ApprovalStore(),
        audit_log=AuditLog(),
        agent_registry=registry,
    )


def _claims() -> GatewayTokenClaims:
    """Build the verified bearer claims a request's context is bound to.

    Returns:
        The claims.
    """
    return GatewayTokenClaims(
        execution_id="exec-1",
        agent_id="agent-1",
        task_id="task-1",
        provider="example-provider",
        model_id="example-medium-001",
    )


async def test_the_context_is_opened_once_per_request() -> None:
    # A batch carrying several ``tools/call`` messages must broker its
    # collaborators once: without the cache every message would rebuild the
    # context, re-resolve the actor and re-broker every connection.
    opener = _context_opener(_context_app_state(deploy_enabled=True), claims=_claims())

    first = await opener()
    second = await opener()

    assert first is second


class TestBuildContext:
    """The wiring that decides what a run's tools can see and reach."""

    async def test_deploy_settings_reach_the_tool_context(self) -> None:
        ctx = await _build_context(
            _context_app_state(deploy_enabled=True, targets="prod, staging"),
            agent_id="agent-1",
            task_id="task-1",
        )
        assert ctx.deploy_targets == frozenset({"prod", "staging"})
        assert ctx.deploy_timeout_seconds == 45.0
        assert ctx.deploy_max_log_chars == 10000

    async def test_publish_settings_reach_the_tool_context(self) -> None:
        ctx = await _build_context(
            _context_app_state(
                deploy_enabled=True,
                targets="prod, staging",
                publish_targets="prod-images, staging-images",
            ),
            agent_id="agent-1",
            task_id="task-1",
        )
        # Sourced from the publish keys, distinct from the deploy targets.
        assert ctx.publish_targets == frozenset({"prod-images", "staging-images"})
        assert ctx.deploy_targets == frozenset({"prod", "staging"})
        assert ctx.publish_timeout_seconds == 60.0
        assert ctx.workspace_root.is_dir()

    async def test_kill_switch_leaves_the_allowlist_populated(self) -> None:
        """The allowlist stays populated; only the switch denies the tools."""
        app_state = _context_app_state(deploy_enabled=False)

        ctx = await _build_context(app_state, agent_id="agent-1", task_id="task-1")
        kill = await _resolve_kill_switches(config_resolver_of(app_state))

        assert kill.deploy_enabled is False
        assert kill.publish_enabled is False
        assert ctx.deploy_targets == frozenset({"prod"})

    async def test_kill_switches_are_read_without_the_tool_context(self) -> None:
        # They gate ``tools/list`` as well as ``tools/call``, and the context is
        # deferred to the one method that executes a tool, so they must resolve
        # on their own rather than out of that bundle.
        kill = await _resolve_kill_switches(
            config_resolver_of(_context_app_state(deploy_enabled=True))
        )

        assert kill.deploy_enabled is True

    async def test_actor_is_none_without_a_registry(self) -> None:
        """A run that cannot be attributed must not be able to deploy."""
        ctx = await _build_context(
            _context_app_state(deploy_enabled=True),
            agent_id="agent-1",
            task_id="task-1",
        )
        assert ctx.actor is None

    async def test_actor_is_resolved_from_the_registry(self) -> None:
        identity = _identity()
        ctx = await _build_context(
            _context_app_state(deploy_enabled=True, registry=_Registry(identity)),
            agent_id="deployer",
            task_id="task-1",
        )
        assert ctx.actor is identity
