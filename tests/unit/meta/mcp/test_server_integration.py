"""Integration-style unit tests for the unified MCP server.

Tests the full stack: registry + scoping + handlers + invoker together.
"""

import json

import pytest

from synthorg.api.state import AppState
from synthorg.meta.chief_of_staff.role import (
    MCP_CAPABILITIES,
    TOOL_ACCESS,
    get_role_definition,
)
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.meta.mcp.scoping import MCPToolScoper
from synthorg.meta.mcp.server import (
    SERVER_NAME,
    get_registry,
    get_server_config,
    reset_singletons,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_singletons()


class TestChiefOfStaffScoping:
    """Test that the CoS sees the right tools via capability scoping."""

    def test_cos_sees_signal_tools(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(MCP_CAPABILITIES)
        names = {t.name for t in visible}
        # All legacy signal tools should be visible
        for tool_name in TOOL_ACCESS:
            assert tool_name in names, f"CoS should see {tool_name}"

    def test_cos_sees_analytics_and_meta(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(MCP_CAPABILITIES)
        names = {t.name for t in visible}
        assert "synthorg_analytics_get_overview" in names
        assert "synthorg_meta_get_config" in names
        assert "synthorg_meta_trigger_cycle" in names

    def test_cos_does_not_see_task_tools(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(MCP_CAPABILITIES)
        names = {t.name for t in visible}
        assert "synthorg_tasks_create" not in names
        assert "synthorg_tasks_delete" not in names

    def test_cos_role_definition_has_mcp_capabilities(self) -> None:
        role_def = get_role_definition()
        assert "mcp_capabilities" in role_def
        caps = role_def["mcp_capabilities"]
        assert isinstance(caps, tuple)
        assert len(caps) > 0


class TestAgentScoping:
    """Test scoping for different agent capability profiles."""

    def test_task_manager_sees_task_tools(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(("tasks:*", "agents:read"))
        names = {t.name for t in visible}
        assert "synthorg_tasks_list" in names
        assert "synthorg_tasks_create" in names
        assert "synthorg_tasks_transition" in names
        assert "synthorg_agents_list" in names
        assert "synthorg_agents_create" not in names  # read only

    def test_read_only_analyst(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(("*:read",))
        names = {t.name for t in visible}
        # Should see read tools
        assert "synthorg_agents_list" in names
        assert "synthorg_tasks_list" in names
        assert "synthorg_budget_get_config" in names
        # Should NOT see write/admin tools
        assert "synthorg_agents_create" not in names
        assert "synthorg_tasks_delete" not in names
        assert "synthorg_settings_update" not in names

    def test_admin_sees_everything(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(("*",))
        assert len(visible) == registry.tool_count

    def test_no_capabilities_sees_nothing(self) -> None:
        registry = build_full_registry()
        scoper = MCPToolScoper(registry)
        visible = scoper.visible_tools(())
        assert len(visible) == 0


class TestFullStackInvocation:
    """End-to-end invocation through all layers."""

    async def test_invoke_via_unified_server(self) -> None:
        """End-to-end dispatch through registry + invoker.

        Uses an unknown tool name so the invoker's not-found branch is
        exercised without needing a live ``app_state`` or depending on
        any handler being a placeholder.
        """
        registry = get_registry()
        handlers = build_handler_map()
        invoker = MCPToolInvoker(registry, handlers)
        result = await invoker.invoke(
            "synthorg_does_not_exist",
            {},
            app_state=mock_of[AppState](),
        )
        assert result.is_error is True
        body = json.loads(result.content)
        assert "Unknown tool" in body["error"]

    def test_server_config_complete(self) -> None:
        config = get_server_config()
        assert config["name"] == SERVER_NAME
        tool_names = config["enabled_tools"]
        assert isinstance(tool_names, list)
        assert "synthorg_signals_get_org_snapshot" in tool_names
        assert "synthorg_tasks_list" in tool_names
        assert "synthorg_agents_list" in tool_names
