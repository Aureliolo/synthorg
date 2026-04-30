"""Meta (self-improvement) domain MCP tools.

Covers the meta controller for the self-improvement cycle.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._simple_args import (
    MetaGetConfigArgs,
    MetaGetMcpServerConfigArgs,
    MetaListMcpToolsArgs,
    MetaListRulesArgs,
    MetaTriggerCycleArgs,
)
from synthorg.meta.mcp.tool_builder import admin_tool, read_tool

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

META_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "meta",
        "get_config",
        "Get the self-improvement configuration.",
        args_model=MetaGetConfigArgs,
    ),
    read_tool(
        "meta",
        "list_rules",
        "List self-improvement rules with their status.",
        args_model=MetaListRulesArgs,
    ),
    read_tool(
        "meta",
        "list_mcp_tools",
        "List available MCP tools and descriptions.",
        args_model=MetaListMcpToolsArgs,
    ),
    read_tool(
        "meta",
        "get_mcp_server_config",
        "Get MCP server configuration metadata.",
        args_model=MetaGetMcpServerConfigArgs,
    ),
    admin_tool(
        "meta",
        "trigger_cycle",
        "Manually trigger a self-improvement cycle.",
        args_model=MetaTriggerCycleArgs,
    ),
)
