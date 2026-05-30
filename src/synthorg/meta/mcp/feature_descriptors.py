# module-kind: code
"""Builder for a feature's MCP handler descriptor.

Each domain feature manifest declares its MCP surface by pairing the
domain's eager ``MCPToolDef`` tuple (cheap data) with a deferred
handler-map loader, so importing a ``feature.py`` during feature
discovery never pulls the service-heavy handler module's import graph.
The composition root's registry + dispatch builders read the descriptors
off discovered features instead of a hand-maintained central list.
"""

from collections.abc import Callable, Mapping

from synthorg._core.features import McpHandlerDescriptor
from synthorg.meta.mcp.registry import MCPToolDef


def mcp_descriptor(
    *,
    domain: str,
    tool_defs: tuple[MCPToolDef, ...],
    handlers: Callable[[], Mapping[str, object]],
) -> McpHandlerDescriptor:
    """Build a feature's :class:`McpHandlerDescriptor`.

    Args:
        domain: Logical MCP domain name (matches the ``domains`` /
            ``handlers`` module stem).
        tool_defs: The domain's tool definitions, eager-imported as
            cheap data so the registry builder has the schemas without a
            handler import.
        handlers: Zero-arg deferred loader returning the domain's
            ``{tool_name: ToolHandler}`` map. Deferred so discovery does
            not import the handler graph.

    Returns:
        Frozen descriptor carrying the tool names + defs + deferred loader.
    """
    return McpHandlerDescriptor(
        domain=domain,
        tool_names=tuple(tool.name for tool in tool_defs),
        tool_defs=tool_defs,
        handlers_factory=handlers,
    )
