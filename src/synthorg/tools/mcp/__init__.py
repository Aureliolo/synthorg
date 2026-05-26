"""MCP bridge -- connects external MCP servers as internal tools.

Re-exports from submodules use lazy ``__getattr__`` to avoid circular
imports. Config models and errors are imported eagerly since they have
no dependency on the tool base classes.
"""

import threading
from typing import TYPE_CHECKING, Final

from .config import MCPConfig, MCPServerConfig
from .errors import (
    MCPConnectionError,
    MCPDiscoveryError,
    MCPError,
    MCPInvocationError,
    MCPTimeoutError,
)
from .models import MCPRawResult, MCPToolInfo

if TYPE_CHECKING:
    from .bridge_tool import MCPBridgeTool
    from .cache import MCPResultCache
    from .client import MCPClient
    from .factory import MCPToolFactory
    from .result_mapper import map_call_tool_result

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


_LAZY_IMPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Lazily import heavy modules on first access.

    The cache assignment into ``globals()`` is protected by a
    threading lock so concurrent first-access from multiple threads
    does not double-import the same submodule and overwrite the
    cached object mid-write.

    Returns:
        Result of type ``object``.

    Raises:
        AttributeError: If the related operation fails.
    """
    if name in _LAZY_IMPORTS:
        import importlib  # noqa: PLC0415

        with _LAZY_IMPORT_LOCK:
            if name in globals():
                return globals()[name]
            module_path, attr_name = _LAZY_IMPORTS[name]
            module = importlib.import_module(module_path, __package__)
            value = getattr(module, attr_name)
            globals()[name] = value
            return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
