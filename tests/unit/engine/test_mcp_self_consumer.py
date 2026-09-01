"""Tests for the agent -> SynthOrg-MCP self-consumer bridge.

Proves: DISABLED is a no-op; trust scoping admits only the operator
allowlist for sub-ELEVATED agents; an ELEVATED agent gets the ambient
(read/write) surface out of the box but sensitive (admin) tools only via
its own grant or an operator broadening; the adapter threads
``app_state`` + ``actor`` into the MCP invoker; and an admin MCP tool
reached by an agent without confirm/reason fails closed via
``require_admin_guardrails``.
"""

import json
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity, ToolPermissions
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.mcp_self_consumer import MCPRateLimiter, build_mcp_self_consumer
from synthorg.security.config import McpSelfConsumerConfig, McpSelfConsumerMode
from synthorg.tools.base import BaseTool, ToolExecutionResult
from tests._shared import mock_of
from tests._shared.fake_clock import FakeClock
from tests._shared.scripted_provider import make_e2e_identity

if TYPE_CHECKING:
    from synthorg.meta.mcp.invoker import MCPToolInvoker

pytestmark = pytest.mark.unit


def make_test_actor() -> AgentIdentity:
    """Minimal AgentIdentity actor for bridge tests."""
    return make_e2e_identity()


def _elevated_actor(*mcp_capabilities: str) -> AgentIdentity:
    """An ELEVATED actor whose MCP grant is exactly ``mcp_capabilities``."""
    base = make_e2e_identity()
    return base.model_copy(
        update={
            "tools": ToolPermissions(
                access_level=ToolAccessLevel.ELEVATED,
                mcp_capabilities=tuple(mcp_capabilities),
            )
        }
    )


_READ_TOOL = "synthorg_tasks_list"
_ADMIN_TOOL = "synthorg_agents_delete"

# Identity-only sentinel: the adapter and builder thread ``app_state``
# through opaquely (the bridge never inspects it). A spec'd mock satisfies
# the invoker's runtime ``app_state: AppState`` check while still serving
# as a stable identity token for the threading assertions.
_SENTINEL_STATE = mock_of[AppState]()


class _RecordingInvoker:
    """Captures invoke() args; not a MagicMock (typed-boundary safe)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        app_state: AppState,
        actor: AgentIdentity | None,
    ) -> ToolExecutionResult:
        self.calls.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "app_state": app_state,
                "actor": actor,
            },
        )
        return ToolExecutionResult(content="ok", is_error=False)


class TestBuildMcpSelfConsumer:
    def test_disabled_returns_none(self) -> None:
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(mode=McpSelfConsumerMode.DISABLED),
            app_state=_SENTINEL_STATE,
        )
        assert provider is None

    def test_sub_elevated_empty_allowlist_yields_nothing(self) -> None:
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(mode=McpSelfConsumerMode.TRUST_SCOPED),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            make_test_actor(), ToolAccessLevel.STANDARD, retrieval_query=None
        )
        assert tools == ()

    def test_sub_elevated_allowlist_admits_only_listed(self) -> None:
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(
                mode=McpSelfConsumerMode.TRUST_SCOPED,
                read_tool_allowlist=(_READ_TOOL,),
            ),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            make_test_actor(), ToolAccessLevel.STANDARD, retrieval_query=None
        )
        assert [t.name for t in tools] == [_READ_TOOL]
        assert all(isinstance(t, BaseTool) for t in tools)
        assert _ADMIN_TOOL not in {t.name for t in tools}

    def test_elevated_gets_full_surface(self) -> None:
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(
                mode=McpSelfConsumerMode.TRUST_SCOPED,
                elevated_capabilities=("*",),
            ),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            make_test_actor(), ToolAccessLevel.ELEVATED, retrieval_query=None
        )
        names = {t.name for t in tools}
        assert len(names) > 1
        assert _READ_TOOL in names

    def test_elevated_ambient_visible_admin_hidden_by_default(self) -> None:
        # Default config (elevated_capabilities=()) + an agent with no
        # grant: the ambient read/write surface is usable out of the box,
        # but sensitive admin tools stay hidden (injection blast-radius).
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(mode=McpSelfConsumerMode.TRUST_SCOPED),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            _elevated_actor(), ToolAccessLevel.ELEVATED, retrieval_query=None
        )
        names = {t.name for t in tools}
        assert _READ_TOOL in names
        assert len(names) > 1
        assert _ADMIN_TOOL not in names

    def test_elevated_granted_admin_capability_visible(self) -> None:
        # An agent granted the admin capability sees that admin tool.
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(mode=McpSelfConsumerMode.TRUST_SCOPED),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            _elevated_actor("agents:admin"),
            ToolAccessLevel.ELEVATED,
            retrieval_query=None,
        )
        names = {t.name for t in tools}
        assert _ADMIN_TOOL in names
        assert _READ_TOOL in names

    def test_elevated_operator_broadening_exposes_admin(self) -> None:
        # An operator can broaden the sensitive surface org-wide without
        # per-agent grants via elevated_capabilities.
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(
                mode=McpSelfConsumerMode.TRUST_SCOPED,
                elevated_capabilities=("agents:admin",),
            ),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            _elevated_actor(), ToolAccessLevel.ELEVATED, retrieval_query=None
        )
        assert _ADMIN_TOOL in {t.name for t in tools}

    def test_denied_tools_excluded_even_when_elevated(self) -> None:
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(
                mode=McpSelfConsumerMode.TRUST_SCOPED,
                elevated_capabilities=("*",),
                denied_tools=(_ADMIN_TOOL,),
            ),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            make_test_actor(), ToolAccessLevel.ELEVATED, retrieval_query=None
        )
        assert _ADMIN_TOOL not in {t.name for t in tools}


class TestMCPRateLimiter:
    def test_burst_then_refuse_then_refill(self) -> None:
        clock = FakeClock()
        # 60/min = 1 token/sec; burst of 2.
        limiter = MCPRateLimiter(per_minute=60, burst=2, clock=clock)
        assert limiter.try_acquire("a", "t") is True
        assert limiter.try_acquire("a", "t") is True
        # Bucket empty -> refused (fail-closed).
        assert limiter.try_acquire("a", "t") is False
        # One second refills one token.
        clock.advance(1.0)
        assert limiter.try_acquire("a", "t") is True

    def test_buckets_are_per_agent_and_tool(self) -> None:
        clock = FakeClock()
        limiter = MCPRateLimiter(per_minute=60, burst=1, clock=clock)
        assert limiter.try_acquire("a", "t") is True
        assert limiter.try_acquire("a", "t") is False
        # A different tool and a different agent have independent budgets.
        assert limiter.try_acquire("a", "other") is True
        assert limiter.try_acquire("b", "t") is True

    def test_disabled_allows_everything(self) -> None:
        limiter = MCPRateLimiter(per_minute=0, burst=1)
        assert all(limiter.try_acquire("a", "t") for _ in range(100))

    def test_bucket_map_is_bounded(self) -> None:
        # A long-lived process with many ephemeral agent ids must not grow
        # the bucket map without bound; the LRU eviction caps it.
        from synthorg.engine.mcp_self_consumer import _MAX_TRACKED_BUCKETS

        clock = FakeClock()
        limiter = MCPRateLimiter(per_minute=60, burst=1, clock=clock)
        for i in range(_MAX_TRACKED_BUCKETS + 50):
            limiter.try_acquire(f"agent-{i}", "t")
        assert len(limiter._buckets) == _MAX_TRACKED_BUCKETS


class TestAdapterRateLimit:
    async def test_over_budget_call_refused(self) -> None:
        from synthorg.engine.mcp_self_consumer import _SynthOrgMCPToolAdapter
        from synthorg.meta.mcp.server import get_registry

        tool_def = get_registry().get(_READ_TOOL)
        limiter = MCPRateLimiter(per_minute=60, burst=1)
        adapter = _SynthOrgMCPToolAdapter(
            mcp_def=tool_def,
            invoker=cast("MCPToolInvoker", _RecordingInvoker()),
            app_state=_SENTINEL_STATE,
            actor=make_test_actor(),
            rate_limiter=limiter,
        )
        first = await adapter.execute(arguments={"limit": 1})
        assert first.is_error is False
        second = await adapter.execute(arguments={"limit": 1})
        assert second.is_error is True
        assert second.metadata["rate_limited"] is True


class TestAdapterThreading:
    async def test_adapter_threads_app_state_and_actor(self) -> None:
        from synthorg.engine.mcp_self_consumer import _SynthOrgMCPToolAdapter
        from synthorg.meta.mcp.server import get_registry

        tool_def = get_registry().get(_READ_TOOL)
        invoker = _RecordingInvoker()
        actor = make_test_actor()
        sentinel_state = _SENTINEL_STATE
        adapter = _SynthOrgMCPToolAdapter(
            mcp_def=tool_def,
            invoker=cast("MCPToolInvoker", invoker),
            app_state=sentinel_state,
            actor=actor,
        )

        result = await adapter.execute(arguments={"limit": 10})

        assert result.is_error is False
        assert len(invoker.calls) == 1
        call = invoker.calls[0]
        assert call["tool_name"] == _READ_TOOL
        assert call["app_state"] is sentinel_state
        assert call["actor"] is actor


_ADMIN_REJECTION_CODES = frozenset({"guardrail_violated", "invalid_argument"})


class TestAdminGuardrailFailsClosed:
    async def test_agent_admin_call_via_bridge_is_blocked(self) -> None:
        # Operator mistakenly allowlists an admin tool for a low-trust
        # agent. The bridge still threads actor, but the confirm/reason
        # guardrail (args-model + require_admin_guardrails) fails the
        # call closed: the agent cannot perform the destructive op.
        provider = build_mcp_self_consumer(
            McpSelfConsumerConfig(
                mode=McpSelfConsumerMode.TRUST_SCOPED,
                read_tool_allowlist=(_ADMIN_TOOL,),
            ),
            app_state=_SENTINEL_STATE,
        )
        assert provider is not None
        tools = provider(
            make_test_actor(), ToolAccessLevel.STANDARD, retrieval_query=None
        )
        assert [t.name for t in tools] == [_ADMIN_TOOL]

        result = await tools[0].execute(
            arguments={"reason": "agent attempted delete"},
        )

        # The MCP layer encodes handler-level domain rejections in the
        # JSON envelope (status="error"); arg-validation failures also
        # set the invoker is_error flag. Either way the destructive op
        # is blocked.
        body = json.loads(result.content)
        assert body["status"] == "error"
        assert body["domain_code"] in _ADMIN_REJECTION_CODES

    async def test_admin_guardrail_rejects_missing_actor(self) -> None:
        # Isolates the actor guardrail the bridge's actor-threading
        # defends against: fully-valid args but actor=None ->
        # require_admin_guardrails rejects with guardrail_violated.
        from synthorg.meta.mcp.server import get_invoker

        result = await get_invoker().invoke(
            _ADMIN_TOOL,
            {
                "confirm": True,
                "reason": "valid reason",
                "agent_name": "some-agent",
            },
            app_state=mock_of[AppState](),
            actor=None,
        )

        body = json.loads(result.content)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"
