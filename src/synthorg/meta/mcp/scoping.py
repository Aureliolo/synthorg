"""MCP tool capability scoping.

Provides ``MCPToolScoper`` which filters tool definitions based on
agent capability declarations.  Supports wildcard matching for
convenient group-based access control.
"""

import fnmatch

from synthorg.core.normalization import normalize_identifier
from synthorg.meta.mcp.registry import DomainToolRegistry, MCPToolDef
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_SCOPING_FILTERED,
)

logger = get_logger(__name__)


class MCPToolScoper:
    """Filters MCP tools based on agent capability declarations.

    Each tool carries a ``capability`` tag in ``domain:action`` format.
    Agents declare their capabilities as a tuple of patterns that may
    include wildcards:

    - ``"tasks:read"`` -- exact match
    - ``"tasks:*"`` -- all actions in the tasks domain
    - ``"*:read"`` -- read actions across all domains
    - ``"*"`` -- everything (admin)

    Resolution order for a given tool (first match wins):

    1. If tool name is in ``denied`` -- **excluded immediately**
    2. Else, if tool name is in ``allowed`` -- **included immediately**
    3. Else, if tool capability matches any ``mcp_capabilities`` pattern -- **included**
    4. Otherwise (no match) -- **excluded**

    Args:
        registry: The domain tool registry to filter from.
    """

    def __init__(self, registry: DomainToolRegistry) -> None:
        self._registry = registry

    def visible_tools(
        self,
        mcp_capabilities: tuple[str, ...],
        *,
        allowed: tuple[str, ...] = (),
        denied: tuple[str, ...] = (),
    ) -> tuple[MCPToolDef, ...]:
        """Return tools visible to an agent with the given capabilities.

        Args:
            mcp_capabilities: Capability patterns the agent has been
                granted (e.g. ``("tasks:*", "agents:read")``).
            allowed: Explicitly allowed tool names (override capabilities).
            denied: Explicitly denied tool names (highest priority).

        Returns:
            Sorted tuple of visible tool definitions.
        """
        denied_lower = frozenset(normalize_identifier(n) for n in denied)
        allowed_lower = frozenset(normalize_identifier(n) for n in allowed)

        all_tools = self._registry.get_all()
        result: list[MCPToolDef] = []

        for tool in all_tools:
            name_lower = normalize_identifier(tool.name)

            # Priority 1: explicit denial
            if name_lower in denied_lower:
                continue

            # Priority 2: explicit allowance
            if name_lower in allowed_lower:
                result.append(tool)
                continue

            # Priority 3: capability pattern matching
            if _matches_any(tool.capability, mcp_capabilities):
                result.append(tool)

        excluded = len(all_tools) - len(result)
        if excluded:
            logger.debug(
                MCP_SCOPING_FILTERED,
                total=len(all_tools),
                visible=len(result),
                excluded=excluded,
                capability_count=len(mcp_capabilities),
            )

        return tuple(result)


def _matches_any(capability: str, patterns: tuple[str, ...]) -> bool:
    """Check if a capability matches any of the given patterns.

    Supports Unix-style wildcards via ``fnmatch``:
    - ``"*"`` matches any capability
    - ``"tasks:*"`` matches ``"tasks:read"``, ``"tasks:write"``
    - ``"*:read"`` matches ``"agents:read"``, ``"tasks:read"``

    Args:
        capability: The tool's capability tag.
        patterns: Patterns to match against.

    Returns:
        ``True`` if any pattern matches.
    """
    cap_lower = normalize_identifier(capability)
    for pattern in patterns:
        pat_lower = normalize_identifier(pattern)
        if fnmatch.fnmatch(cap_lower, pat_lower):
            return True
    return False
