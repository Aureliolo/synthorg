"""Shared fixtures for MCP bridge unit tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.mcp.cache import MCPResultCache
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig
from synthorg.tools.mcp.models import MCPRawResult, MCPToolInfo

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


@pytest.fixture
def http_server_config() -> MCPServerConfig:
    """Minimal streamable HTTP server config."""
    return MCPServerConfig(
        name="test-http",
        transport="streamable_http",
        url="http://localhost:8080/mcp",
    )


@pytest.fixture
def disabled_server_config() -> MCPServerConfig:
    """Disabled server config."""
    return MCPServerConfig(
        name="test-disabled",
        transport="stdio",
        command="noop",
        enabled=False,
    )


@pytest.fixture
def sample_mcp_config(
    stdio_server_config: MCPServerConfig,
    http_server_config: MCPServerConfig,
) -> MCPConfig:
    """Config with two enabled servers."""
    return MCPConfig(
        servers=(stdio_server_config, http_server_config),
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


@pytest.fixture
def sample_raw_result() -> MCPRawResult:
    """Sample raw MCP result with no content."""
    return MCPRawResult()


@pytest.fixture
def sample_execution_result() -> ToolExecutionResult:
    """Sample tool execution result."""
    return ToolExecutionResult(content="hello world")


# ── Mock MCP session ─────────────────────────────────────────────


def _make_mock_mcp_tool(
    name: str = "mock-tool",
    description: str = "A mock tool",
    input_schema: dict[str, Any] | None = None,
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
    content: list[Any] | None = None,
    is_error: bool = False,
    structured_content: dict[str, Any] | None = None,
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
def mock_session() -> AsyncMock:
    """Mock MCP ClientSession."""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(
        return_value=_make_mock_list_tools_result(),
    )
    session.call_tool = AsyncMock(
        return_value=_make_mock_call_tool_result(),
    )
    return session


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
