"""MCP tool handler aggregator.

Imports all domain handler modules and builds a unified handler map
keyed by tool name (matching ``MCPToolDef.handler_key``).
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.meta.mcp.handlers.agents import AGENT_HANDLERS
from synthorg.meta.mcp.handlers.analytics import ANALYTICS_HANDLERS
from synthorg.meta.mcp.handlers.approvals import APPROVAL_HANDLERS
from synthorg.meta.mcp.handlers.budget import BUDGET_HANDLERS
from synthorg.meta.mcp.handlers.communication import COMMUNICATION_HANDLERS
from synthorg.meta.mcp.handlers.coordination import COORDINATION_HANDLERS
from synthorg.meta.mcp.handlers.docs import DOCS_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure import INFRASTRUCTURE_HANDLERS
from synthorg.meta.mcp.handlers.integrations import INTEGRATION_HANDLERS
from synthorg.meta.mcp.handlers.memory import MEMORY_HANDLERS
from synthorg.meta.mcp.handlers.meta import META_HANDLERS
from synthorg.meta.mcp.handlers.organization import ORGANIZATION_HANDLERS
from synthorg.meta.mcp.handlers.quality import QUALITY_HANDLERS
from synthorg.meta.mcp.handlers.signals import SIGNAL_HANDLERS
from synthorg.meta.mcp.handlers.tasks import TASK_HANDLERS
from synthorg.meta.mcp.handlers.workflows import WORKFLOW_HANDLERS
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLERS_BUILT

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.meta.mcp.handler_protocol import ToolHandler

logger = get_logger(__name__)

_ALL_HANDLER_MAPS: tuple[Mapping[str, ToolHandler], ...] = (
    SIGNAL_HANDLERS,
    AGENT_HANDLERS,
    TASK_HANDLERS,
    WORKFLOW_HANDLERS,
    APPROVAL_HANDLERS,
    BUDGET_HANDLERS,
    ORGANIZATION_HANDLERS,
    COORDINATION_HANDLERS,
    ANALYTICS_HANDLERS,
    MEMORY_HANDLERS,
    QUALITY_HANDLERS,
    META_HANDLERS,
    COMMUNICATION_HANDLERS,
    INTEGRATION_HANDLERS,
    INFRASTRUCTURE_HANDLERS,
    DOCS_HANDLERS,
)


def build_handler_map() -> Mapping[str, ToolHandler]:
    """Build a unified handler map from all domain handler modules.

    Returns:
        Read-only mapping of handler keys to handler functions.

    Raises:
        ValueError: If duplicate handler keys are found.
    """
    handlers: dict[str, ToolHandler] = {}
    for handler_map in _ALL_HANDLER_MAPS:
        for key, handler in handler_map.items():
            if key in handlers:
                msg = (
                    f"Duplicate handler key {key!r} -- check domain "
                    f"handler modules for conflicting registrations"
                )
                logger.error(
                    MCP_HANDLERS_BUILT,
                    error=msg,
                    duplicate_key=key,
                )
                raise ValueError(msg)
            handlers[key] = handler
    logger.debug(
        MCP_HANDLERS_BUILT,
        handler_count=len(handlers),
    )
    return MappingProxyType(handlers)
