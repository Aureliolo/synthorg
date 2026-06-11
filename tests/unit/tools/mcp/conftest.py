"""Shared fixtures for MCP bridge unit tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.tools.mcp.cache import MCPResultCache
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPServerConfig
from synthorg.tools.mcp.models import MCPToolInfo
from tests._shared import JsonDict

# ── Sample configs ───────────────────────────────────────────────


@pytest.fixture
def stdio_server_config() -> MCPServerConfig:
    """Minimal stdio server config."""
    return MCPServerConfig(
        name="test-stdio",
        transport="stdio",
        command="echo",
        args=("hello",),
    )


# ── Sample models ────────────────────────────────────────────────


@pytest.fixture
def sample_tool_info() -> MCPToolInfo:
    """Sample discovered tool metadata."""
    return MCPToolInfo(
        name="test-tool",
        description="A test tool",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        server_name="test-server",
    )


# ── Mock MCP session ─────────────────────────────────────────────


def _make_mock_mcp_tool(
    name: str = "mock-tool",
    description: str = "A mock tool",
    input_schema: JsonDict | None = None,
) -> MagicMock:
    """Create a mock MCP Tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {
        "type": "object",
        "properties": {"input": {"type": "string"}},
    }
    return tool


def _make_mock_list_tools_result(
    tools: list[MagicMock] | None = None,
) -> MagicMock:
    """Create a mock ListToolsResult."""
    result = MagicMock()
    result.tools = tools or [_make_mock_mcp_tool()]
    return result


def _make_mock_call_tool_result(
    content: list[object] | None = None,
    is_error: bool = False,
    structured_content: JsonDict | None = None,
) -> MagicMock:
    """Create a mock CallToolResult."""
    from mcp.types import TextContent

    result = MagicMock()
    result.content = content or [
        TextContent(type="text", text="result text"),
    ]
    result.isError = is_error
    result.structuredContent = structured_content
    return result


@pytest.fixture
def mock_client(
    stdio_server_config: MCPServerConfig,
) -> MCPClient:
    """MCPClient with mocked internals for unit testing."""
    client = MCPClient(stdio_server_config)
    # Manually set session to simulate connected state
    mock_session = AsyncMock()
    mock_session.list_tools = AsyncMock(
        return_value=_make_mock_list_tools_result(),
    )
    mock_session.call_tool = AsyncMock(
        return_value=_make_mock_call_tool_result(),
    )
    client._session = mock_session
    return client


@pytest.fixture
def result_cache() -> MCPResultCache:
    """Small result cache for testing."""
    return MCPResultCache(max_size=4, ttl_seconds=1.0)
