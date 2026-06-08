"""MCP tool handler dispatch table, assembled from feature discovery.

``build_handler_map`` walks ``discover_features()`` and merges each
feature's deferred ``handlers_factory()`` map into a single dispatch
table keyed by tool name (matching ``MCPToolDef.handler_key``). A
duplicate key across features is a wiring error and raises.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from synthorg._core.features import discover_features
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLERS_BUILT

logger = get_logger(__name__)


def build_handler_map() -> Mapping[str, ToolHandler]:
    """Build a unified handler map from every feature's MCP descriptor.

    Returns:
        Read-only mapping of handler keys to handler functions.

    Raises:
        ValueError: If duplicate handler keys are found.
    """
    handlers: dict[str, ToolHandler] = {}
    for feature in discover_features():
        for descriptor in feature.mcp_handlers:
            factory = descriptor.handlers_factory
            if factory is None:
                continue
            for key, handler in factory().items():
                if key in handlers:
                    msg = (
                        f"Duplicate handler key {key!r} -- check feature "
                        f"manifests for conflicting MCP registrations"
                    )
                    logger.error(
                        MCP_HANDLERS_BUILT,
                        error=msg,
                        duplicate_key=key,
                    )
                    raise ValueError(msg)
                handlers[key] = cast("ToolHandler", handler)
    logger.debug(
        MCP_HANDLERS_BUILT,
        handler_count=len(handlers),
    )
    return MappingProxyType(handlers)
