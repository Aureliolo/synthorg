"""Temporary equivalence guard: discovery MCP surface == hand-listed.

Proves every feature manifest's :class:`McpHandlerDescriptor` faithfully
mirrors the hand-maintained ``ALL_DOMAIN_TOOLS`` / ``_ALL_HANDLER_MAPS``
aggregators before the composition root flips ``build_full_registry`` /
``build_handler_map`` to discovery and the hand-lists are deleted. This is
the oracle for the blind-swap risk: while it is green, the discovery-derived
tool-def set and handler-key set are byte-identical to what the live
hand-listed builders produce, so the flip cannot silently drift the surface.

Replace with the static ``tool_count`` + known-set assertions once the
discovery flip lands (the hand-lists no longer exist to compare against).
"""

from typing import cast

import pytest

from synthorg._core.features import discover_features
from synthorg.meta.mcp.domains import ALL_DOMAIN_TOOLS, build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.meta.mcp.registry import MCPToolDef

pytestmark = pytest.mark.unit


def _discovery_tool_names() -> set[str]:
    """Collect tool names from every discovered feature's ``tool_defs``.

    Returns:
        The set of tool names declared across feature manifests.
    """
    names: set[str] = set()
    for feature in discover_features():
        for descriptor in feature.mcp_handlers:
            names.update(cast(MCPToolDef, td).name for td in descriptor.tool_defs)
    return names


def _discovery_handler_keys() -> set[str]:
    """Collect handler keys from every discovered feature's loader.

    Returns:
        The set of handler keys produced by the deferred loaders.
    """
    keys: set[str] = set()
    for feature in discover_features():
        for descriptor in feature.mcp_handlers:
            factory = descriptor.handlers_factory
            if factory is not None:
                keys.update(factory().keys())
    return keys


def test_discovery_tool_defs_match_hand_listed_registry() -> None:
    """Discovery tool-def names equal the hand-listed registry's tools."""
    hand_listed = {tool.name for tools in ALL_DOMAIN_TOOLS for tool in tools}
    registry_names = set(build_full_registry().get_names())
    discovered = _discovery_tool_names()
    # Sanity-pin the hand-list against the live registry, then assert
    # discovery is a faithful mirror of both.
    assert hand_listed == registry_names
    assert discovered == hand_listed


def test_discovery_handler_keys_match_hand_listed_map() -> None:
    """Discovery handler keys equal the hand-listed handler map's keys."""
    hand_listed = set(build_handler_map().keys())
    assert _discovery_handler_keys() == hand_listed
