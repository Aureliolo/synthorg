"""Integration test: MCP bridge full pipeline with mock server."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import TextContent

from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.mcp.bridge_tool import MCPBridgeTool
from synthorg.tools.mcp.cache import MCPResultCache
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPServerConfig

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


def _make_connected_client(
    config: MCPServerConfig,
    tools: list[MagicMock],
    call_result: MagicMock,
) -> MCPClient:
    """Create a mock-connected MCPClient."""
    client = MCPClient(config)
    session = AsyncMock()

    list_result = MagicMock()
    list_result.tools = tools
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock(return_value=call_result)
    client._session = session
    return client


class TestMCPBridgeFullPipeline:
    """End-to-end: discover -> bridge -> execute -> map result."""

    async def test_discover_and_execute_tool(self) -> None:
        """Full pipeline: discover tool, create bridge, execute."""
        config = MCPServerConfig(
            name="test-server",
            transport="stdio",
            command="echo",
        )

        # Mock MCP tool
        mock_tool = MagicMock()
        mock_tool.name = "search"
        mock_tool.description = "Search documents"
        mock_tool.inputSchema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        }

        # Mock call result
        call_result = MagicMock()
        call_result.content = [
            TextContent(type="text", text="Found 3 results"),
        ]
        call_result.isError = False
        call_result.structuredContent = None

        client = _make_connected_client(
            config,
            [mock_tool],
            call_result,
        )

        # Discover
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"

        # Create bridge
        bridge = MCPBridgeTool(
            tool_info=tools[0],
            client=client,
        )
        assert bridge.name == "mcp_test-server_search"

        # Execute
        result = await bridge.execute(
            arguments={"query": "test"},
        )
        assert isinstance(result, ToolExecutionResult)
        assert result.content == "Found 3 results"
        assert not result.is_error

    async def test_pipeline_with_cache(self) -> None:
        """Full pipeline with result caching."""
        config = MCPServerConfig(
            name="cached-server",
            transport="stdio",
            command="echo",
        )

        mock_tool = MagicMock()
        mock_tool.name = "lookup"
        mock_tool.description = "Lookup"
        mock_tool.inputSchema = {}

        call_result = MagicMock()
        call_result.content = [
            TextContent(type="text", text="cached result"),
        ]
        call_result.isError = False
        call_result.structuredContent = None

        client = _make_connected_client(
            config,
            [mock_tool],
            call_result,
        )

        tools = await client.list_tools()
        cache = MCPResultCache(max_size=10, ttl_seconds=60.0)

        bridge = MCPBridgeTool(
            tool_info=tools[0],
            client=client,
            cache=cache,
        )

        # First call hits server
        r1 = await bridge.execute(arguments={})
        assert r1.content == "cached result"

        # Change server response
        new_result = MagicMock()
        new_result.content = [
            TextContent(type="text", text="new result"),
        ]
        new_result.isError = False
        new_result.structuredContent = None
        client._session.call_tool = AsyncMock(  # type: ignore[method-assign, union-attr]
            return_value=new_result,
        )

        # Second call should use cache
        r2 = await bridge.execute(arguments={})
        assert r2.content == "cached result"

    async def test_pipeline_with_error_result(self) -> None:
        """Pipeline handles MCP error results correctly."""
        config = MCPServerConfig(
            name="err-server",
            transport="stdio",
            command="echo",
        )

        mock_tool = MagicMock()
        mock_tool.name = "failing"
        mock_tool.description = "Might fail"
        mock_tool.inputSchema = {}

        call_result = MagicMock()
        call_result.content = [
            TextContent(type="text", text="Permission denied"),
        ]
        call_result.isError = True
        call_result.structuredContent = None

        client = _make_connected_client(
            config,
            [mock_tool],
            call_result,
        )

        tools = await client.list_tools()
        bridge = MCPBridgeTool(
            tool_info=tools[0],
            client=client,
        )

        result = await bridge.execute(arguments={})
        assert result.is_error
        assert result.content == "Permission denied"

    async def test_pipeline_with_filters(self) -> None:
        """Pipeline respects enabled/disabled filters."""
        config = MCPServerConfig(
            name="filter-server",
            transport="stdio",
            command="echo",
            enabled_tools=("allowed",),
            disabled_tools=(),
        )

        tool_allowed = MagicMock()
        tool_allowed.name = "allowed"
        tool_allowed.description = "Allowed tool"
        tool_allowed.inputSchema = {}

        tool_blocked = MagicMock()
        tool_blocked.name = "blocked"
        tool_blocked.description = "Blocked tool"
        tool_blocked.inputSchema = {}

        call_result = MagicMock()
        call_result.content = [
            TextContent(type="text", text="ok"),
        ]
        call_result.isError = False
        call_result.structuredContent = None

        client = _make_connected_client(
            config,
            [tool_allowed, tool_blocked],
            call_result,
        )

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "allowed"
