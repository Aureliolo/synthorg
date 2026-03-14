"""Tests for MCPBridgeTool."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.mcp.bridge_tool import MCPBridgeTool
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPServerConfig
from synthorg.tools.mcp.errors import MCPInvocationError
from synthorg.tools.mcp.models import MCPToolInfo

if TYPE_CHECKING:
    from synthorg.tools.mcp.cache import MCPResultCache

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.fixture
def bridge_tool(
    sample_tool_info: MCPToolInfo,
    mock_client: MCPClient,
) -> MCPBridgeTool:
    """Bridge tool without cache."""
    return MCPBridgeTool(
        tool_info=sample_tool_info,
        client=mock_client,
    )


@pytest.fixture
def bridge_tool_with_cache(
    sample_tool_info: MCPToolInfo,
    mock_client: MCPClient,
    result_cache: MCPResultCache,
) -> MCPBridgeTool:
    """Bridge tool with cache."""
    return MCPBridgeTool(
        tool_info=sample_tool_info,
        client=mock_client,
        cache=result_cache,
    )


class TestBridgeToolConstruction:
    """Name construction and properties."""

    def test_name_format(
        self,
        bridge_tool: MCPBridgeTool,
    ) -> None:
        assert bridge_tool.name == "mcp_test-server_test-tool"

    def test_category_is_mcp(
        self,
        bridge_tool: MCPBridgeTool,
    ) -> None:
        assert bridge_tool.category == ToolCategory.MCP

    def test_description_from_tool_info(
        self,
        bridge_tool: MCPBridgeTool,
    ) -> None:
        assert bridge_tool.description == "A test tool"

    def test_parameters_schema_from_tool_info(
        self,
        bridge_tool: MCPBridgeTool,
    ) -> None:
        schema = bridge_tool.parameters_schema
        assert schema is not None
        assert "properties" in schema

    def test_tool_info_property(
        self,
        bridge_tool: MCPBridgeTool,
        sample_tool_info: MCPToolInfo,
    ) -> None:
        assert bridge_tool.tool_info == sample_tool_info

    def test_empty_input_schema_yields_none_parameters(self) -> None:
        tool_info = MCPToolInfo(
            name="no-schema",
            description="No schema",
            input_schema={},
            server_name="srv",
        )
        config = MCPServerConfig(
            name="srv",
            transport="stdio",
            command="echo",
        )
        client = MCPClient(config)
        client._session = AsyncMock()
        bridge = MCPBridgeTool(
            tool_info=tool_info,
            client=client,
        )
        assert bridge.parameters_schema is None


class TestBridgeToolExecute:
    """Execute delegation to client."""

    async def test_execute_calls_client(
        self,
        bridge_tool: MCPBridgeTool,
    ) -> None:
        result = await bridge_tool.execute(
            arguments={"query": "test"},
        )
        assert isinstance(result, ToolExecutionResult)
        assert result.content == "result text"

    async def test_execute_returns_error_on_mcp_failure(
        self,
        bridge_tool: MCPBridgeTool,
        mock_client: MCPClient,
    ) -> None:
        mock_client._session.call_tool.side_effect = MCPInvocationError(  # type: ignore[union-attr]
            "invocation error",
            context={"server": "test", "tool": "test"},
        )
        result = await bridge_tool.execute(arguments={})
        assert result.is_error
        assert "invocation error" in result.content


class TestBridgeToolWithCache:
    """Cache integration."""

    async def test_cache_miss_calls_client(
        self,
        bridge_tool_with_cache: MCPBridgeTool,
        mock_client: MCPClient,
    ) -> None:
        result = await bridge_tool_with_cache.execute(
            arguments={"q": "first"},
        )
        assert result.content == "result text"
        mock_client._session.call_tool.assert_called_once()  # type: ignore[union-attr]

    async def test_cache_hit_skips_client(
        self,
        bridge_tool_with_cache: MCPBridgeTool,
        mock_client: MCPClient,
    ) -> None:
        # First call populates cache
        await bridge_tool_with_cache.execute(
            arguments={"q": "cached"},
        )
        mock_client._session.call_tool.reset_mock()  # type: ignore[union-attr]

        # Second call should use cache
        result = await bridge_tool_with_cache.execute(
            arguments={"q": "cached"},
        )
        assert result.content == "result text"
        mock_client._session.call_tool.assert_not_called()  # type: ignore[union-attr]

    async def test_different_args_not_cached(
        self,
        bridge_tool_with_cache: MCPBridgeTool,
        mock_client: MCPClient,
    ) -> None:
        await bridge_tool_with_cache.execute(arguments={"q": "a"})
        mock_client._session.call_tool.reset_mock()  # type: ignore[union-attr]

        await bridge_tool_with_cache.execute(arguments={"q": "b"})
        mock_client._session.call_tool.assert_called_once()  # type: ignore[union-attr]

    async def test_unhashable_arguments_bypass_cache(
        self,
        sample_tool_info: MCPToolInfo,
        mock_client: MCPClient,
        result_cache: MCPResultCache,
    ) -> None:
        """Unhashable args (e.g. custom objects) don't crash execution."""

        class Unhashable:
            __hash__ = None  # type: ignore[assignment]

        bridge = MCPBridgeTool(
            tool_info=sample_tool_info,
            client=mock_client,
            cache=result_cache,
        )
        result = await bridge.execute(
            arguments={"obj": Unhashable()},
        )
        assert isinstance(result, ToolExecutionResult)
        assert not result.is_error

    async def test_error_results_not_cached(
        self,
        sample_tool_info: MCPToolInfo,
        mock_client: MCPClient,
        result_cache: MCPResultCache,
    ) -> None:
        """Error results should not be stored in the cache."""
        mock_client._session.call_tool.side_effect = MCPInvocationError(  # type: ignore[union-attr]
            "transient error",
            context={"server": "test", "tool": "test"},
        )
        bridge = MCPBridgeTool(
            tool_info=sample_tool_info,
            client=mock_client,
            cache=result_cache,
        )
        result = await bridge.execute(arguments={"q": "fail"})
        assert result.is_error
        # Cache should be empty — error not cached
        assert result_cache.get("test-tool", {"q": "fail"}) is None
