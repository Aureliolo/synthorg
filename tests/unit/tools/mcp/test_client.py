"""Tests for MCPClient."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPServerConfig
from synthorg.tools.mcp.errors import (
    MCPConnectionError,
    MCPDiscoveryError,
    MCPInvocationError,
    MCPTimeoutError,
)
from synthorg.tools.mcp.models import MCPToolInfo

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestMCPClientConnection:
    """Connection lifecycle tests."""

    def test_not_connected_initially(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        assert not client.is_connected

    def test_server_name_property(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        assert client.server_name == "test-stdio"

    async def test_connect_sets_session(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()

        with (
            patch(
                "synthorg.tools.mcp.client.stdio_client",
            ) as mock_stdio,
            patch(
                "synthorg.tools.mcp.client.ClientSession",
            ) as mock_cls,
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(
                return_value=(AsyncMock(), AsyncMock()),
            )
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_stdio.return_value = mock_cm

            session_cm = AsyncMock()
            session_cm.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            session_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = session_cm

            await client.connect()

        assert client.is_connected

    async def test_connect_timeout_raises_mcp_connection_error(self) -> None:
        config = MCPServerConfig(
            name="slow-server",
            transport="stdio",
            command="echo",
            connect_timeout_seconds=0.01,
        )
        client = MCPClient(config)

        async def hang_forever(*_a: object, **_kw: object) -> None:
            await asyncio.sleep(100)

        with (
            patch.object(
                client,
                "_connect_with_stack",
                side_effect=hang_forever,
            ),
            pytest.raises(MCPConnectionError, match="timed out"),
        ):
            await client.connect()

    async def test_connect_failure_raises_mcp_connection_error(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        with (
            patch(
                "synthorg.tools.mcp.client.stdio_client",
                side_effect=OSError("connection refused"),
            ),
            pytest.raises(MCPConnectionError, match="refused"),
        ):
            await client.connect()

    async def test_disconnect_clears_session(
        self,
        mock_client: MCPClient,
    ) -> None:
        assert mock_client.is_connected
        mock_client._exit_stack = AsyncMock()
        mock_client._exit_stack.aclose = AsyncMock()
        await mock_client.disconnect()
        assert not mock_client.is_connected

    async def test_disconnect_when_not_connected(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        # Should not raise
        await client.disconnect()
        assert not client.is_connected

    async def test_disconnect_clears_state_on_aclose_error(
        self,
        mock_client: MCPClient,
    ) -> None:
        mock_client._exit_stack = AsyncMock()
        mock_client._exit_stack.aclose = AsyncMock(
            side_effect=RuntimeError("cleanup failed"),
        )
        # Should not raise — error is logged and swallowed
        await mock_client.disconnect()
        assert not mock_client.is_connected
        assert mock_client._exit_stack is None

    def test_config_property(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        assert client.config is stdio_server_config


class TestMCPClientListTools:
    """Tool discovery tests."""

    async def test_list_tools_returns_tool_info(
        self,
        mock_client: MCPClient,
    ) -> None:
        tools = await mock_client.list_tools()
        assert len(tools) == 1
        assert isinstance(tools[0], MCPToolInfo)
        assert tools[0].name == "mock-tool"
        assert tools[0].server_name == "test-stdio"

    async def test_list_tools_not_connected_raises(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        with pytest.raises(MCPConnectionError, match="Not connected"):
            await client.list_tools()

    async def test_list_tools_discovery_error(
        self,
        mock_client: MCPClient,
    ) -> None:
        mock_client._session.list_tools.side_effect = RuntimeError(  # type: ignore[union-attr]
            "discovery failed",
        )
        with pytest.raises(MCPDiscoveryError, match="discovery failed"):
            await mock_client.list_tools()

    async def test_list_tools_applies_enabled_filter(self) -> None:
        config = MCPServerConfig(
            name="filtered",
            transport="stdio",
            command="echo",
            enabled_tools=("allowed-tool",),
        )
        client = MCPClient(config)
        mock_session = AsyncMock()

        tool1 = MagicMock()
        tool1.name = "allowed-tool"
        tool1.description = "allowed"
        tool1.inputSchema = {}

        tool2 = MagicMock()
        tool2.name = "blocked-tool"
        tool2.description = "blocked"
        tool2.inputSchema = {}

        mock_result = MagicMock()
        mock_result.tools = [tool1, tool2]
        mock_session.list_tools = AsyncMock(return_value=mock_result)
        client._session = mock_session

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "allowed-tool"

    async def test_list_tools_applies_disabled_filter(self) -> None:
        config = MCPServerConfig(
            name="filtered",
            transport="stdio",
            command="echo",
            disabled_tools=("blocked-tool",),
        )
        client = MCPClient(config)
        mock_session = AsyncMock()

        tool1 = MagicMock()
        tool1.name = "allowed-tool"
        tool1.description = "allowed"
        tool1.inputSchema = {}

        tool2 = MagicMock()
        tool2.name = "blocked-tool"
        tool2.description = "blocked"
        tool2.inputSchema = {}

        mock_result = MagicMock()
        mock_result.tools = [tool1, tool2]
        mock_session.list_tools = AsyncMock(return_value=mock_result)
        client._session = mock_session

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "allowed-tool"


class TestMCPClientCallTool:
    """Tool invocation tests."""

    async def test_call_tool_returns_raw_result(
        self,
        mock_client: MCPClient,
    ) -> None:
        result = await mock_client.call_tool("mock-tool", {"a": 1})
        assert len(result.content) == 1
        assert not result.is_error

    async def test_call_tool_not_connected_raises(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        with pytest.raises(MCPConnectionError, match="Not connected"):
            await client.call_tool("tool", {})

    async def test_call_tool_timeout_raises(
        self,
        mock_client: MCPClient,
    ) -> None:
        mock_client._session.call_tool.side_effect = TimeoutError()  # type: ignore[union-attr]
        with pytest.raises(MCPTimeoutError, match="timed out"):
            await mock_client.call_tool("slow-tool", {})

    async def test_call_tool_error_raises(
        self,
        mock_client: MCPClient,
    ) -> None:
        mock_client._session.call_tool.side_effect = RuntimeError(  # type: ignore[union-attr]
            "invocation failed",
        )
        with pytest.raises(
            MCPInvocationError,
            match="invocation failed",
        ):
            await mock_client.call_tool("bad-tool", {})


class TestMCPClientReconnect:
    """Reconnect behavior."""

    async def test_reconnect_disconnects_then_connects(
        self,
        mock_client: MCPClient,
    ) -> None:
        mock_client._exit_stack = AsyncMock()
        mock_client._exit_stack.aclose = AsyncMock()

        with (
            patch.object(
                mock_client,
                "disconnect",
                new_callable=AsyncMock,
            ) as mock_disconnect,
            patch.object(
                mock_client,
                "connect",
                new_callable=AsyncMock,
            ) as mock_connect,
        ):
            await mock_client.reconnect()
            mock_disconnect.assert_called_once()
            mock_connect.assert_called_once()


class TestMCPClientContextManager:
    """Async context manager protocol."""

    async def test_context_manager(
        self,
        stdio_server_config: MCPServerConfig,
    ) -> None:
        client = MCPClient(stdio_server_config)
        with (
            patch.object(
                client,
                "connect",
                new_callable=AsyncMock,
            ) as mock_connect,
            patch.object(
                client,
                "disconnect",
                new_callable=AsyncMock,
            ) as mock_disconnect,
        ):
            async with client as c:
                assert c is client
                mock_connect.assert_called_once()
            mock_disconnect.assert_called_once()


class TestMCPClientHTTPTransport:
    """HTTP transport connection path."""

    async def test_connect_http_sets_session(self) -> None:
        config = MCPServerConfig(
            name="test-http",
            transport="streamable_http",
            url="http://localhost:8080/mcp",
        )
        client = MCPClient(config)
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()

        with (
            patch(
                "synthorg.tools.mcp.client.streamablehttp_client",
            ) as mock_http,
            patch(
                "synthorg.tools.mcp.client.ClientSession",
            ) as mock_cls,
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(
                return_value=(AsyncMock(), AsyncMock(), AsyncMock()),
            )
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = mock_cm

            session_cm = AsyncMock()
            session_cm.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            session_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = session_cm

            await client.connect()

        assert client.is_connected
