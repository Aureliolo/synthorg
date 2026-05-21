"""Unified MCP API server setup.

Registers all SynthOrg API tools as an internal MCP server that agents
and external users connect to. Each agent sees a capability-scoped
subset of the full tool catalog.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.meta.mcp.scoping import MCPToolScoper
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import DomainToolRegistry
    from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry

logger = get_logger(__name__)

# Server metadata.
SERVER_NAME = "synthorg-api"
SERVER_DESCRIPTION = (
    "Unified SynthOrg API server exposing all framework capabilities "
    "as MCP tools with capability-based scoping. Agents see only tools "
    "matching their declared mcp_capabilities."
)

# Tool name prefix for all synthorg MCP tools.
TOOL_PREFIX = "synthorg"

# Module-level singletons (built on first access).
# These are initialized during single-threaded application startup
# (via Litestar lifespan).  Not thread-safe for concurrent first-call
# races; if that becomes a requirement, guard with threading.Lock.
_registry: DomainToolRegistry | None = None
_scoper: MCPToolScoper | None = None
_invoker: MCPToolInvoker | None = None
# Set by the toolsmith boot hook (install_dynamic_tool_layer). When
# present, the invoker dispatches over the static surface layered with
# the live authored-tool registry, so runtime-authored tools become
# reachable without unfreezing the static registry.
_dynamic_registry: DynamicToolRegistry | None = None


def get_registry() -> DomainToolRegistry:
    """Return the global tool registry (built and frozen on first call).

    Returns:
        Frozen ``DomainToolRegistry`` with all tools.
    """
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = build_full_registry()
    return _registry


def get_scoper() -> MCPToolScoper:
    """Return the global tool scoper.

    Returns:
        ``MCPToolScoper`` backed by the global registry.
    """
    global _scoper  # noqa: PLW0603
    if _scoper is None:
        _scoper = MCPToolScoper(get_registry())
    return _scoper


def get_invoker() -> MCPToolInvoker:
    """Return the global tool invoker.

    Returns:
        ``MCPToolInvoker`` with all handlers registered.
    """
    global _invoker  # noqa: PLW0603
    if _invoker is None:
        if _dynamic_registry is not None:
            from synthorg.meta.toolsmith.dynamic_registry import (  # noqa: PLC0415
                LayeredHandlerMap,
                LayeredToolRegistry,
            )

            _invoker = MCPToolInvoker(
                LayeredToolRegistry(get_registry(), _dynamic_registry),
                LayeredHandlerMap(build_handler_map(), _dynamic_registry),
            )
        else:
            _invoker = MCPToolInvoker(get_registry(), build_handler_map())
    return _invoker


def install_dynamic_tool_layer(dynamic_registry: DynamicToolRegistry) -> None:
    """Install the live authored-tool registry behind the static surface.

    Called once by the toolsmith boot hook. Resets the cached invoker so
    the next :func:`get_invoker` rebuilds over the layered registry; the
    invoker then sees authored tools as they are registered into
    ``dynamic_registry`` because the layered reader consults it live.
    """
    global _dynamic_registry, _invoker  # noqa: PLW0603
    _dynamic_registry = dynamic_registry
    _invoker = None


def get_server_config() -> dict[str, object]:
    """Return MCP server configuration for registration.

    Returns:
        Server config dict compatible with MCPServerConfig.
    """
    registry = get_registry()
    tool_names = list(registry.get_names())
    return {
        "name": SERVER_NAME,
        "description": SERVER_DESCRIPTION,
        "transport": "stdio",
        "enabled": True,
        "enabled_tools": tool_names,
        "tool_prefix": TOOL_PREFIX,
        "tool_count": registry.tool_count,
    }


def reset_singletons() -> None:
    """Reset module-level singletons (for testing only).

    This allows tests to rebuild the registry, scoper, and invoker
    without polluting other test runs.
    """
    global _registry, _scoper, _invoker, _dynamic_registry  # noqa: PLW0603
    _registry = None
    _scoper = None
    _invoker = None
    _dynamic_registry = None
