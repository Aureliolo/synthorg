"""The demo feature's MCP surface: one read-only ``greet`` tool.

Defined in the feature's own directory (not ``meta/mcp/``) to prove a feature
declares its whole MCP surface locally; the discovery-based registry +
dispatch builders pick it up off the manifest with no central edits.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg._demo.state import DemoStateSlice
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import capability_gap, err, ok
from synthorg.meta.mcp.handlers.common_logging import log_handler_invoke_failed
from synthorg.meta.mcp.registry import MCPToolDef
from synthorg.meta.mcp.tool_builder import read_tool

_GREET_TOOL = "synthorg_demo_greet"

DEMO_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "demo",
        "greet",
        "Return the demo feature's greeting. A read-only discovery smoke tool.",
    ),
)


async def _demo_greet(
    *,
    app_state: AppStateSliceMixin,
    arguments: Mapping[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return the demo greeting as an MCP ``ok`` envelope.

    Args:
        app_state: Application state providing slice access.
        arguments: Parsed tool arguments (the demo tool takes none).
        actor: Calling agent identity (unused by the demo).

    Returns:
        A JSON ``ok`` envelope carrying the greeting, a ``capability_gap``
        envelope when the demo service is not wired, or an ``err`` envelope
        when the service raises.
    """
    del arguments, actor
    try:
        service = app_state.slice(DemoStateSlice).service
        if service is None:
            return capability_gap(_GREET_TOOL, "demo service not wired")
        return ok({"greeting": service.greet().greeting})
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_GREET_TOOL, exc)
        return err(exc)


DEMO_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType({_GREET_TOOL: _demo_greet})
