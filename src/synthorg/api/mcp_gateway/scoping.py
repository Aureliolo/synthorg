# module-kind: code
"""Tool-discovery and kill-switch surface for the credentialed-tool server.

The governed invoke path in :mod:`~synthorg.api.mcp_gateway.tools` owns the
registry and the per-actor visibility primitive (:func:`visible_tool_names`);
this module sits one level above it, resolving what a ``tools/list`` returns
and which families a request-scoped kill switch denies. Keeping it a separate
module leaves the registry-plus-invoke module lean and the dependency one-way
(this module imports the registry, never the reverse).
"""

from typing import Final

from synthorg.api.mcp_gateway.tools import CREDENTIALED_TOOLS, visible_tool_names

# The deploy family, keyed off its own capability domain rather than a
# hand-maintained name list, so a future deploy tool is gated by the kill
# switch automatically.
_DEPLOY_TOOL_NAMES: Final[tuple[str, ...]] = tuple(
    tool.name for tool in CREDENTIALED_TOOLS if tool.capability.startswith("deploy:")
)


def deploy_denials(*, deploy_enabled: bool) -> tuple[str, ...]:
    """Return the deploy tool names to deny when the deploy family is disabled.

    The ``deploy_tools_enabled`` setting is a defence-in-depth kill switch:
    even with a ``deploy:*`` capability grant, a disabled family denies every
    deploy tool (from both discovery and dispatch), so turning the family off
    takes effect the next request without touching the capability grant.

    Args:
        deploy_enabled: Whether the deploy tool family is enabled this request.

    Returns:
        An empty tuple when enabled; every deploy tool name when disabled.
    """
    return () if deploy_enabled else _DEPLOY_TOOL_NAMES


def tool_schemas(
    capabilities: tuple[str, ...],
    *,
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Return MCP tool schemas for the tools visible under *capabilities*.

    Visibility is resolved exactly as at call time (:func:`visible_tool_names`),
    so a tool the actor may not invoke never appears in ``tools/list`` either.
    A disabled family (e.g. ``deploy_tools_enabled`` off) is threaded here as a
    ``denied`` set so its tools vanish from discovery, not merely from dispatch.

    Args:
        capabilities: Capability patterns the actor is granted.
        allowed: Explicit tool-name allowances (override capabilities).
        denied: Explicit tool-name denials (highest priority).

    Returns:
        A list of ``{name, description, inputSchema}`` MCP tool descriptors.
    """
    visible = visible_tool_names(
        capabilities=capabilities, allowed=allowed, denied=denied
    )
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.args_model.model_json_schema(),
        }
        for spec in CREDENTIALED_TOOLS
        if spec.name in visible
    ]


__all__ = ["deploy_denials", "tool_schemas"]
