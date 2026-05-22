"""Domain tool definition aggregator.

Imports all domain modules and builds a unified ``DomainToolRegistry``
containing every MCP tool definition.
"""

from synthorg.meta.mcp.domains.agents import AGENT_TOOLS
from synthorg.meta.mcp.domains.analytics import ANALYTICS_TOOLS
from synthorg.meta.mcp.domains.approvals import APPROVAL_TOOLS
from synthorg.meta.mcp.domains.budget import BUDGET_TOOLS
from synthorg.meta.mcp.domains.communication import COMMUNICATION_TOOLS
from synthorg.meta.mcp.domains.coordination import COORDINATION_TOOLS
from synthorg.meta.mcp.domains.docs import DOCS_TOOLS
from synthorg.meta.mcp.domains.infrastructure import INFRASTRUCTURE_TOOLS
from synthorg.meta.mcp.domains.integrations import INTEGRATION_TOOLS
from synthorg.meta.mcp.domains.knowledge import KNOWLEDGE_TOOLS
from synthorg.meta.mcp.domains.memory import MEMORY_TOOLS
from synthorg.meta.mcp.domains.meta import META_TOOLS
from synthorg.meta.mcp.domains.organization import ORGANIZATION_TOOLS
from synthorg.meta.mcp.domains.quality import QUALITY_TOOLS
from synthorg.meta.mcp.domains.research import RESEARCH_TOOLS
from synthorg.meta.mcp.domains.signals import SIGNAL_MCP_TOOLS
from synthorg.meta.mcp.domains.tasks import TASK_TOOLS
from synthorg.meta.mcp.domains.workflows import WORKFLOW_TOOLS
from synthorg.meta.mcp.registry import DomainToolRegistry, MCPToolDef
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_REGISTRY_BUILT

logger = get_logger(__name__)

ALL_DOMAIN_TOOLS: tuple[tuple[MCPToolDef, ...], ...] = (
    SIGNAL_MCP_TOOLS,
    AGENT_TOOLS,
    TASK_TOOLS,
    WORKFLOW_TOOLS,
    APPROVAL_TOOLS,
    BUDGET_TOOLS,
    ORGANIZATION_TOOLS,
    COORDINATION_TOOLS,
    ANALYTICS_TOOLS,
    MEMORY_TOOLS,
    QUALITY_TOOLS,
    META_TOOLS,
    COMMUNICATION_TOOLS,
    INTEGRATION_TOOLS,
    INFRASTRUCTURE_TOOLS,
    DOCS_TOOLS,
    KNOWLEDGE_TOOLS,
    RESEARCH_TOOLS,
)


def build_full_registry() -> DomainToolRegistry:
    """Build and freeze a registry containing all domain tools.

    Returns:
        Frozen ``DomainToolRegistry`` with every tool registered.
    """
    registry = DomainToolRegistry()
    for domain_tools in ALL_DOMAIN_TOOLS:
        registry.register_many(domain_tools)
    registry.freeze()
    logger.debug(
        MCP_REGISTRY_BUILT,
        tool_count=registry.tool_count,
        domain_count=len(ALL_DOMAIN_TOOLS),
    )
    return registry
