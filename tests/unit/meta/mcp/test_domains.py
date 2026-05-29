"""Unit tests for MCP domain tool definitions.

Validates that all domain tools follow conventions: unique names,
correct naming pattern, valid capabilities, and required fields.
"""

import re

import pytest

from synthorg._core.features import discover_features
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.domains.agents import AGENT_TOOLS
from synthorg.meta.mcp.domains.analytics import ANALYTICS_TOOLS
from synthorg.meta.mcp.domains.approvals import APPROVAL_TOOLS
from synthorg.meta.mcp.domains.budget import BUDGET_TOOLS
from synthorg.meta.mcp.domains.communication import COMMUNICATION_TOOLS
from synthorg.meta.mcp.domains.coordination import COORDINATION_TOOLS
from synthorg.meta.mcp.domains.infrastructure import INFRASTRUCTURE_TOOLS
from synthorg.meta.mcp.domains.integrations import INTEGRATION_TOOLS
from synthorg.meta.mcp.domains.memory import MEMORY_TOOLS
from synthorg.meta.mcp.domains.meta import META_TOOLS
from synthorg.meta.mcp.domains.organization import ORGANIZATION_TOOLS
from synthorg.meta.mcp.domains.quality import QUALITY_TOOLS
from synthorg.meta.mcp.domains.signals import SIGNAL_MCP_TOOLS
from synthorg.meta.mcp.domains.tasks import TASK_TOOLS
from synthorg.meta.mcp.domains.workflows import WORKFLOW_TOOLS
from synthorg.meta.mcp.registry import MCPToolDef

pytestmark = pytest.mark.unit

# Pattern: synthorg_{domain}_{action}
NAME_PATTERN = re.compile(r"^synthorg_[a-z][a-z0-9_]*_[a-z][a-z0-9_]*$")
# Pattern: domain:action
CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")


def _all_tools() -> list[MCPToolDef]:
    """Flatten every discovered feature's MCP tool defs into a single list."""
    return list(build_full_registry().as_mapping().values())


class TestAllDomainTools:
    """Cross-domain validation tests."""

    def test_all_names_unique(self) -> None:
        tools = _all_tools()
        names = [t.name for t in tools]
        duplicates = [n for n in names if names.count(n) > 1]
        assert not duplicates, f"Duplicate tool names: {set(duplicates)}"

    def test_all_names_follow_convention(self) -> None:
        for tool in _all_tools():
            assert NAME_PATTERN.match(tool.name), (
                f"Tool name {tool.name!r} does not match "
                f"synthorg_{{domain}}_{{action}} pattern"
            )

    def test_all_capabilities_follow_convention(self) -> None:
        for tool in _all_tools():
            assert CAPABILITY_PATTERN.match(tool.capability), (
                f"Tool {tool.name!r} capability {tool.capability!r} "
                f"does not match domain:action pattern"
            )

    def test_all_have_descriptions(self) -> None:
        for tool in _all_tools():
            assert len(tool.description) > 10, (
                f"Tool {tool.name!r} has too-short description"
            )

    def test_all_have_parameters(self) -> None:
        for tool in _all_tools():
            assert tool.parameters.get("type") == "object", (
                f"Tool {tool.name!r} parameters must be type: object"
            )
            assert "properties" in tool.parameters, (
                f"Tool {tool.name!r} parameters must have properties"
            )

    def test_all_have_handler_keys(self) -> None:
        for tool in _all_tools():
            assert tool.handler_key, f"Tool {tool.name!r} has no handler_key"

    def test_build_full_registry(self) -> None:
        registry = build_full_registry()
        assert registry.frozen is True
        assert registry.tool_count == len(_all_tools())

    def test_total_tool_count(self) -> None:
        """Sanity check: we should have a substantial number of tools."""
        tools = _all_tools()
        assert len(tools) >= 150, f"Expected at least 150 tools, got {len(tools)}"

    def test_no_empty_mcp_descriptors(self) -> None:
        for feature in discover_features():
            for descriptor in feature.mcp_handlers:
                assert len(descriptor.tool_defs) > 0, (
                    f"Feature {feature.name!r} MCP descriptor for domain "
                    f"{descriptor.domain!r} has no tool defs"
                )


class TestSignalDomain:
    """Signal domain specific tests."""

    def test_signal_tool_count(self) -> None:
        assert len(SIGNAL_MCP_TOOLS) == 9

    def test_signal_names_match_legacy(self) -> None:
        """Signal tool names must match the existing synthorg_signals_ prefix."""
        for tool in SIGNAL_MCP_TOOLS:
            assert tool.name.startswith("synthorg_signals_")


class TestAgentDomain:
    """Agent domain specific tests."""

    def test_agent_crud_tools_exist(self) -> None:
        names = {t.name for t in AGENT_TOOLS}
        assert "synthorg_agents_list" in names
        assert "synthorg_agents_get" in names
        assert "synthorg_agents_create" in names
        assert "synthorg_agents_update" in names
        assert "synthorg_agents_delete" in names

    def test_agent_observability_tools_exist(self) -> None:
        names = {t.name for t in AGENT_TOOLS}
        assert "synthorg_agents_get_performance" in names
        assert "synthorg_agents_get_health" in names


class TestTaskDomain:
    """Task domain specific tests."""

    def test_task_crud_tools_exist(self) -> None:
        names = {t.name for t in TASK_TOOLS}
        assert "synthorg_tasks_list" in names
        assert "synthorg_tasks_get" in names
        assert "synthorg_tasks_create" in names
        assert "synthorg_tasks_transition" in names
        assert "synthorg_tasks_cancel" in names


class TestWorkflowDomain:
    """Workflow domain specific tests."""

    def test_workflow_crud_present(self) -> None:
        names = {t.name for t in WORKFLOW_TOOLS}
        assert "synthorg_workflows_list" in names
        assert "synthorg_workflows_create" in names

    def test_subworkflow_tools_present(self) -> None:
        names = {t.name for t in WORKFLOW_TOOLS}
        assert "synthorg_subworkflows_list" in names

    def test_execution_tools_present(self) -> None:
        names = {t.name for t in WORKFLOW_TOOLS}
        assert "synthorg_workflow_executions_list" in names
        assert "synthorg_workflow_executions_start" in names


# Minimum tool counts per domain -- derived from the number of API
# controller endpoints in each domain.  These are floor values (new
# tools may be added, but existing ones must not be removed).
@pytest.mark.parametrize(
    ("domain_tools", "expected_min"),
    [
        (SIGNAL_MCP_TOOLS, 9),  # 8 read + 1 write (submit_proposal)
        (AGENT_TOOLS, 15),  # identity, personality, training, etc.
        (TASK_TOOLS, 8),  # tasks + activities CRUD
        (WORKFLOW_TOOLS, 15),  # workflows, subworkflows, executions, versions
        (APPROVAL_TOOLS, 5),  # approval lifecycle
        (BUDGET_TOOLS, 5),  # overview, limits, forecast
        (ORGANIZATION_TOOLS, 15),  # company, depts, teams
        (COORDINATION_TOOLS, 8),  # metrics, scaling, ceremony
        (ANALYTICS_TOOLS, 7),  # metrics, reports, dashboards
        (MEMORY_TOOLS, 10),  # fine-tune, checkpoints, search
        (QUALITY_TOOLS, 8),  # reviews, evaluations, config
        (META_TOOLS, 5),  # self-improvement, proposals
        (COMMUNICATION_TOOLS, 18),  # messaging, meetings, webhooks
        (INTEGRATION_TOOLS, 18),  # MCP catalog, OAuth, clients
        (INFRASTRUCTURE_TOOLS, 35),  # health, settings, providers
    ],
    ids=[
        "signals",
        "agents",
        "tasks",
        "workflows",
        "approvals",
        "budget",
        "organization",
        "coordination",
        "analytics",
        "memory",
        "quality",
        "meta",
        "communication",
        "integrations",
        "infrastructure",
    ],
)
class TestDomainToolCounts:
    """Verify each domain has a minimum expected tool count."""

    def test_minimum_tools(
        self,
        domain_tools: tuple[MCPToolDef, ...],
        expected_min: int,
    ) -> None:
        assert len(domain_tools) >= expected_min, (
            f"Expected >= {expected_min} tools, got {len(domain_tools)}"
        )
