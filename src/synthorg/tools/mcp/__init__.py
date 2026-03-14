"""MCP bridge — connects external MCP servers as internal tools.

Re-exports from submodules use lazy ``__getattr__`` to avoid circular
imports. Config models and errors are imported eagerly since they have
no dependency on the tool base classes.
"""

from .config import MCPConfig, MCPServerConfig
from .errors import (
    MCPConnectionError,
    MCPDiscoveryError,
    MCPError,
    MCPInvocationError,
    MCPTimeoutError,
)
from .models import MCPRawResult, MCPToolInfo

__all__ = [
    "MCPBridgeTool",
    "MCPClient",
    "MCPConfig",
    "MCPConnectionError",
    "MCPDiscoveryError",
    "MCPError",
    "MCPInvocationError",
    "MCPRawResult",
    "MCPResultCache",
    "MCPServerConfig",
    "MCPTimeoutError",
    "MCPToolFactory",
    "MCPToolInfo",
    "map_call_tool_result",
]

# Lazy imports for types that depend on tools.base / MCP SDK
# to break the circular import chain.
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "MCPBridgeTool": (".bridge_tool", "MCPBridgeTool"),
    "MCPClient": (".client", "MCPClient"),
    "MCPResultCache": (".cache", "MCPResultCache"),
    "MCPToolFactory": (".factory", "MCPToolFactory"),
    "map_call_tool_result": (
        ".result_mapper",
        "map_call_tool_result",
    ),
}


def __getattr__(name: str) -> object:
    """Lazily import heavy modules on first access."""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib  # noqa: PLC0415

        module = importlib.import_module(module_path, __package__)
        value = getattr(module, attr_name)
        # Cache on the module dict to avoid repeated lookups
        globals()[name] = value
        return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
