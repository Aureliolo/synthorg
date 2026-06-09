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

from typing import TYPE_CHECKING, Protocol, cast, override

from synthorg.core.agent import AgentIdentity
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.meta.mcp.registry import MCPToolDef
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.config import McpSelfConsumerConfig, McpSelfConsumerMode
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from pydantic import JsonValue

    from synthorg.api.state import AppState
    from synthorg.meta.mcp.invoker import MCPToolInvoker


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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Invoke the MCP tool, threading app_state + actor.

        Returns:
            The :class:`ToolExecutionResult` from the underlying MCP
            tool invoker.
        """
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
        get_scoper,
    )

    invoker = get_invoker()
    scoper = get_scoper()

    def _provide(
        identity: AgentIdentity,
        access_level: ToolAccessLevel,
    ) -> tuple[BaseTool, ...]:
        if access_level is ToolAccessLevel.ELEVATED:
            capabilities = config.elevated_capabilities
            allowed: tuple[str, ...] = ()
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
            )
            for tool_def in visible
        )

    return _provide
