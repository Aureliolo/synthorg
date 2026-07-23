"""Agent -> SynthOrg-MCP self-consumer bridge.

A running agent can call SynthOrg's *own* MCP tools through its
ordinary tool invoker, scoped by the agent's earned trust level. The
``actor`` (the calling :class:`AgentIdentity`) is threaded into every
invocation so the per-handler ``require_admin_guardrails`` check fails
closed for an agent that reaches an admin/destructive tool without an
explicit ``confirm`` + ``reason``.

The factory closes over ``app_state`` (which the engine layer does not
otherwise hold) and the MCP server singletons, and returns a provider
callable the engine's tool-invoker factory calls per agent. Safe
default: ``McpSelfConsumerMode.DISABLED`` -> no provider, no MCP
surface exposed to agents.
"""

import threading
from typing import TYPE_CHECKING, Final, Protocol, override

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.meta.mcp.registry import MCPToolDef
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.config import McpSelfConsumerConfig, McpSelfConsumerMode
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.meta.mcp.invoker import MCPToolInvoker

# The capability-tag suffix marking a sensitive (admin) MCP tool: the
# high-blast-radius surface gated behind an explicit per-agent grant.
_ADMIN_SUFFIX: Final[str] = ":admin"
_SECONDS_PER_MINUTE: Final[float] = 60.0


class MCPRateLimiter:
    """Fail-closed per-(agent, tool) token bucket for MCP tool calls.

    A runaway agent loop can hammer a single MCP tool; a blocking rate
    limiter would only slow it. This bucket instead *refuses* a call over
    budget (returns ``False``), so the agent gets a retryable error and
    the loop cannot monopolise a tool. ``per_minute <= 0`` disables it.
    """

    def __init__(
        self, *, per_minute: int, burst: int, clock: Clock | None = None
    ) -> None:
        self._enabled = per_minute > 0
        self._rate_per_sec = per_minute / _SECONDS_PER_MINUTE
        self._capacity = float(burst)
        self._clock = clock or SystemClock()
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def try_acquire(self, agent_id: str, tool_name: str) -> bool:
        """Consume one token for ``(agent_id, tool_name)``.

        Returns:
            ``True`` when a token was available (call permitted), ``False``
            when the bucket is empty (call refused, fail-closed).
        """
        if not self._enabled:
            return True
        now = self._clock.monotonic()
        key = (agent_id, tool_name)
        with self._lock:
            tokens, last = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate_per_sec)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True


class MCPSelfConsumerProvider(Protocol):
    """Per-agent factory of trust-scoped SynthOrg-MCP bridge tools."""

    def __call__(
        self,
        identity: AgentIdentity,
        access_level: ToolAccessLevel,
    ) -> tuple[BaseTool, ...]:
        """Return the MCP tools visible to *identity* at *access_level*.

        Returns:
            Tuple of :class:`BaseTool` adapters scoped to the agent's
            earned trust level.
        """
        ...


class _SynthOrgMCPToolAdapter(BaseTool):
    """Engine ``BaseTool`` wrapping one SynthOrg MCP tool.

    Delegates to ``MCPToolInvoker.invoke`` with ``app_state`` bound at
    boot and ``actor`` set to the calling agent, so destructive
    handlers attribute (and fail-closed gate) the agent correctly.
    """

    def __init__(
        self,
        *,
        mcp_def: MCPToolDef,
        invoker: MCPToolInvoker,
        app_state: AppState,
        actor: AgentIdentity,
        rate_limiter: MCPRateLimiter | None = None,
    ) -> None:
        super().__init__(
            name=mcp_def.name,
            description=mcp_def.description,
            parameters_schema=mcp_def.parameters,
            category=ToolCategory.MCP,
        )
        self._mcp_def = mcp_def
        self._invoker = invoker
        self._app_state = app_state
        self._actor = actor
        self._rate_limiter = rate_limiter

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Invoke the MCP tool, threading app_state + actor.

        A per-(agent, tool) rate check runs first: a call over budget is
        refused with a retryable error so a runaway loop cannot hammer one
        tool.

        Returns:
            The :class:`ToolExecutionResult` from the underlying MCP
            tool invoker, or a rate-limited error result.
        """
        if self._rate_limiter is not None and not self._rate_limiter.try_acquire(
            str(self._actor.id), self._mcp_def.name
        ):
            return ToolExecutionResult(
                content=(
                    f"MCP tool {self._mcp_def.name!r} rate limit exceeded; "
                    "slow down and retry shortly."
                ),
                is_error=True,
                metadata={"rate_limited": True},
            )
        return await self._invoker.invoke(
            self._mcp_def.name,
            arguments,
            app_state=self._app_state,
            actor=self._actor,
        )


def build_mcp_self_consumer(
    config: McpSelfConsumerConfig,
    app_state: AppState,
) -> MCPSelfConsumerProvider | None:
    """Build the agent -> SynthOrg-MCP provider, or ``None`` if disabled.

    Args:
        config: The ``SecurityConfig.mcp_self_consumer`` block.
        app_state: Live application state, bound into every bridge tool
            so MCP handlers reach their service layers.

    Returns:
        A provider callable, or ``None`` when the mode is
        ``DISABLED`` (the safe default -- agents get no MCP surface).
    """
    if config.mode is McpSelfConsumerMode.DISABLED:
        return None

    from synthorg.meta.mcp.server import (  # noqa: PLC0415
        get_invoker,
        get_registry,
        get_scoper,
    )

    invoker = get_invoker()
    scoper = get_scoper()
    registry = get_registry()
    rate_limiter = MCPRateLimiter(
        per_minute=config.rate_limit_per_minute,
        burst=config.rate_limit_burst,
    )

    # An admin-capability tool (``domain:admin``) is *sensitive*: it is the
    # high-blast-radius surface (firing staff, deploying, deleting) that a
    # prompt-injected agent must not be able to reach unless the agent was
    # explicitly granted it. Every other tool is *ambient* (read/write) and
    # usable out of the box by any ELEVATED agent, so the surface works with
    # zero per-agent configuration while dangerous tools stay gated.
    ambient_names: tuple[str, ...] = tuple(
        tool.name
        for tool in registry.get_all()
        if not tool.capability.endswith(_ADMIN_SUFFIX)
    )

    def _provide(
        identity: AgentIdentity,
        access_level: ToolAccessLevel,
    ) -> tuple[BaseTool, ...]:
        if access_level is ToolAccessLevel.ELEVATED:
            # Visible = ambient (always) UNION the sensitive tools this
            # agent earned (its own ``mcp_capabilities``) UNION any
            # operator-set org-wide broadening. Out of the box an agent's
            # grant is empty, so it gets exactly the ambient surface.
            capabilities = tuple(
                {*identity.tools.mcp_capabilities, *config.elevated_capabilities}
            )
            allowed = ambient_names
        else:
            # Sub-ELEVATED agents get no capability-pattern access;
            # only the explicit operator allowlist (empty by default
            # -> no MCP for low-trust agents).
            capabilities = ()
            allowed = config.read_tool_allowlist
        visible = scoper.visible_tools(
            capabilities,
            allowed=allowed,
            denied=config.denied_tools,
        )
        return tuple(
            _SynthOrgMCPToolAdapter(
                mcp_def=tool_def,
                invoker=invoker,
                app_state=app_state,
                actor=identity,
                rate_limiter=rate_limiter,
            )
            for tool_def in visible
        )

    return _provide
