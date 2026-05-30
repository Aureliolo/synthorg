"""Domain tool-definition registry, assembled from feature discovery.

``build_full_registry`` walks ``discover_features()`` and registers each
feature's MCP ``tool_defs`` so the registry is composed from the feature
manifests rather than a hand-maintained central list.
"""

from typing import TYPE_CHECKING, cast

from synthorg._core.features import discover_features
from synthorg.meta.mcp.registry import DomainToolRegistry
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_REGISTRY_BUILT

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

logger = get_logger(__name__)


def build_full_registry() -> DomainToolRegistry:
    """Build and freeze a registry containing every feature's MCP tools.

    Iterates discovered features and registers each MCP descriptor's
    ``tool_defs``; the registry's own duplicate-name guard rejects a
    tool claimed by two features.

    Returns:
        Frozen ``DomainToolRegistry`` with every tool registered.
    """
    registry = DomainToolRegistry()
    domain_count = 0
    for feature in discover_features():
        for descriptor in feature.mcp_handlers:
            registry.register_many(
                cast("tuple[MCPToolDef, ...]", descriptor.tool_defs),
            )
            domain_count += 1
    registry.freeze()
    logger.debug(
        MCP_REGISTRY_BUILT,
        tool_count=registry.tool_count,
        domain_count=domain_count,
    )
    return registry
