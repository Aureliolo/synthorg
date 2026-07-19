"""Tests for MCPToolFactory."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.tools.mcp.bridge_tool import MCPBridgeTool
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig
from synthorg.tools.mcp.errors import MCPConnectionError
from synthorg.tools.mcp.factory import MCPToolFactory
from synthorg.tools.mcp.models import MCPToolInfo

pytestmark = pytest.mark.unit


def _make_mock_client(
    server_name: str,
    tools: tuple[MCPToolInfo, ...],
    config: MCPServerConfig | None = None,
) -> MCPClient:
    """Create a mock MCPClient whose ``list_tools`` returns ``tools``."""
    if config is None:
        config = MCPServerConfig(
            name=server_name,
            transport="stdio",
            command="echo",
        )
    client = MCPClient(config)
    client._session = AsyncMock()
    client.list_tools = AsyncMock(return_value=tools)  # type: ignore[method-assign]
    return client


class TestFactoryCreateTools:
    """Tool discovery and creation."""

    async def test_create_tools_from_single_server(self) -> None:
        config = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="srv1",
                    transport="stdio",
                    command="echo",
                ),
            ),
        )
        factory = MCPToolFactory(config)

        mock_tools = (
            MCPToolInfo(
                name="tool-a",
                description="Tool A",
                server_name="srv1",
            ),
        )

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            new_callable=AsyncMock,
        ) as mock_cad:
            mock_client = _make_mock_client("srv1", mock_tools)
            mock_cad.return_value = (mock_client, mock_tools)
            tools = await factory.create_tools()

        assert len(tools) == 1
        assert isinstance(tools[0], MCPBridgeTool)
        assert tools[0].name == "mcp_srv1_tool-a"

    async def test_create_tools_from_multiple_servers(self) -> None:
        config = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="srv1",
                    transport="stdio",
                    command="echo",
                ),
                MCPServerConfig(
                    name="srv2",
                    transport="streamable_http",
                    url="http://localhost",
                ),
            ),
        )
        factory = MCPToolFactory(config)

        tools1 = (
            MCPToolInfo(
                name="tool-a",
                description="A",
                server_name="srv1",
            ),
        )
        tools2 = (
            MCPToolInfo(
                name="tool-b",
                description="B",
                server_name="srv2",
            ),
            MCPToolInfo(
                name="tool-c",
                description="C",
                server_name="srv2",
            ),
        )

        call_count = 0

        async def mock_connect_discover(
            cfg: MCPServerConfig,
        ) -> tuple[MCPClient, tuple[MCPToolInfo, ...]]:
            nonlocal call_count
            call_count += 1
            if cfg.name == "srv1":
                return (_make_mock_client("srv1", tools1), tools1)
            return (_make_mock_client("srv2", tools2, cfg), tools2)

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            side_effect=mock_connect_discover,
        ):
            tools = await factory.create_tools()

        assert len(tools) == 3
        assert call_count == 2

    async def test_one_failed_server_does_not_drop_others(self) -> None:
        """Per-server failure isolation: a broken server must not blank the rest."""
        config = MCPConfig(
            servers=(
                MCPServerConfig(name="bad", transport="stdio", command="echo"),
                MCPServerConfig(name="good", transport="stdio", command="echo"),
            ),
        )
        factory = MCPToolFactory(config)
        good_tools = (MCPToolInfo(name="tool-a", description="A", server_name="good"),)

        async def mock_connect_discover(
            cfg: MCPServerConfig,
        ) -> tuple[MCPClient, tuple[MCPToolInfo, ...]]:
            if cfg.name == "bad":
                msg = "boom"
                raise MCPConnectionError(msg, context={"server": "bad"})
            return (_make_mock_client("good", good_tools, cfg), good_tools)

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            side_effect=mock_connect_discover,
        ):
            tools = await factory.create_tools()

        # The good server's tool survives; no exception despite the bad one.
        assert [t.name for t in tools] == ["mcp_good_tool-a"]

    async def test_skip_disabled_servers(self) -> None:
        config = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="enabled",
                    transport="stdio",
                    command="echo",
                ),
                MCPServerConfig(
                    name="disabled",
                    transport="stdio",
                    command="echo",
                    enabled=False,
                ),
            ),
        )
        factory = MCPToolFactory(config)

        tools1 = (
            MCPToolInfo(
                name="tool-a",
                description="A",
                server_name="enabled",
            ),
        )

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            new_callable=AsyncMock,
        ) as mock_cad:
            mock_client = _make_mock_client("enabled", tools1)
            mock_cad.return_value = (mock_client, tools1)
            tools = await factory.create_tools()

        assert len(tools) == 1
        # Only called once (disabled server skipped)
        mock_cad.assert_called_once()

    async def test_empty_config_returns_empty(self) -> None:
        config = MCPConfig()
        factory = MCPToolFactory(config)
        tools = await factory.create_tools()
        assert tools == ()


class TestFactoryShutdown:
    """Client lifecycle management."""

    async def test_shutdown_disconnects_all_clients(self) -> None:
        config = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="srv1",
                    transport="stdio",
                    command="echo",
                ),
            ),
        )
        factory = MCPToolFactory(config)

        tools1 = (
            MCPToolInfo(
                name="tool-a",
                description="A",
                server_name="srv1",
            ),
        )

        mock_client = _make_mock_client("srv1", tools1)
        mock_client.disconnect = AsyncMock()  # type: ignore[method-assign]

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            new_callable=AsyncMock,
            return_value=(mock_client, tools1),
        ):
            await factory.create_tools()

        await factory.shutdown()
        mock_client.disconnect.assert_called_once()

    async def test_shutdown_clears_client_list(self) -> None:
        config = MCPConfig()
        factory = MCPToolFactory(config)
        client = MagicMock(spec=MCPClient)
        client.disconnect = AsyncMock()
        factory._clients = [client]

        await factory.shutdown()
        assert factory._clients == []


class TestFactoryReuseGuard:
    """Cannot call create_tools twice."""

    async def test_create_tools_twice_raises(self) -> None:
        config = MCPConfig()
        factory = MCPToolFactory(config)
        await factory.create_tools()
        with pytest.raises(RuntimeError, match="must not be called more than once"):
            await factory.create_tools()


class TestFactoryPartialFailureIsolation:
    """A failed server is isolated: the healthy one keeps working."""

    async def test_partial_failure_keeps_healthy_server(self) -> None:
        config = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="ok-srv",
                    transport="stdio",
                    command="echo",
                ),
                MCPServerConfig(
                    name="bad-srv",
                    transport="stdio",
                    command="echo",
                ),
            ),
        )
        factory = MCPToolFactory(config)
        ok_tools = (MCPToolInfo(name="tool-a", description="A", server_name="ok-srv"),)
        ok_client = _make_mock_client("ok-srv", ok_tools)
        ok_client.disconnect = AsyncMock()  # type: ignore[method-assign]

        msg = "server down"

        async def mock_connect_discover(
            cfg: MCPServerConfig,
        ) -> tuple[MCPClient, tuple[MCPToolInfo, ...]]:
            if cfg.name == "ok-srv":
                return (ok_client, ok_tools)
            raise ConnectionError(msg)

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            side_effect=mock_connect_discover,
        ):
            tools = await factory.create_tools()

        # The healthy server's tools survive; it is NOT torn down mid-create
        # (only shutdown() disconnects it), and no exception propagates.
        assert [t.name for t in tools] == ["mcp_ok-srv_tool-a"]
        ok_client.disconnect.assert_not_called()


class TestFactoryServerStatuses:
    """Per-server connect status is surfaced, not just an empty tool tuple."""

    async def test_status_records_connected_and_failed(self) -> None:
        config = MCPConfig(
            servers=(
                MCPServerConfig(name="ok-srv", transport="stdio", command="echo"),
                MCPServerConfig(name="bad-srv", transport="stdio", command="echo"),
            ),
        )
        factory = MCPToolFactory(config)
        ok_tools = (MCPToolInfo(name="tool-a", description="A", server_name="ok-srv"),)
        ok_client = _make_mock_client("ok-srv", ok_tools)

        async def mock_connect_discover(
            cfg: MCPServerConfig,
        ) -> tuple[MCPClient, tuple[MCPToolInfo, ...]]:
            if cfg.name == "ok-srv":
                return (ok_client, ok_tools)
            msg = "server down"
            raise MCPConnectionError(msg, context={"server": cfg.name})

        with patch.object(
            MCPToolFactory,
            "_connect_and_discover",
            side_effect=mock_connect_discover,
        ):
            await factory.create_tools()

        statuses = {s.name: s for s in factory.server_statuses}
        assert statuses["ok-srv"].connected is True
        assert statuses["ok-srv"].tool_count == 1
        assert statuses["bad-srv"].connected is False
        assert statuses["bad-srv"].error is not None

    async def test_ping_reports_live_liveness(self) -> None:
        config = MCPConfig(
            servers=(MCPServerConfig(name="srv", transport="stdio", command="echo"),),
        )
        factory = MCPToolFactory(config)
        tools = (MCPToolInfo(name="t", description="d", server_name="srv"),)
        client = _make_mock_client("srv", tools)
        factory._clients = [client]

        statuses = await factory.ping_servers()
        assert len(statuses) == 1
        assert statuses[0].connected is True
        assert statuses[0].tool_count == 1

    async def test_ping_reports_dead_server(self) -> None:
        config = MCPConfig()
        factory = MCPToolFactory(config)
        client = MagicMock(spec=MCPClient)
        client.server_name = "srv"
        client.list_tools = AsyncMock(
            side_effect=MCPConnectionError("dead", context={"server": "srv"}),
        )
        factory._clients = [client]

        statuses = await factory.ping_servers()
        assert statuses[0].connected is False
        assert statuses[0].error is not None


class TestFactoryShutdownSwallowsErrors:
    """Shutdown continues when one client fails to disconnect."""

    async def test_shutdown_continues_after_disconnect_error(self) -> None:
        config = MCPConfig()
        factory = MCPToolFactory(config)

        client1 = MagicMock(spec=MCPClient)
        client1.disconnect = AsyncMock(
            side_effect=RuntimeError("disconnect broke"),
        )
        client1.server_name = "client1"
        client2 = MagicMock(spec=MCPClient)
        client2.disconnect = AsyncMock()
        client2.server_name = "client2"

        factory._clients = [client1, client2]
        await factory.shutdown()

        client1.disconnect.assert_called_once()
        client2.disconnect.assert_called_once()
        assert factory._clients == []


class TestFactoryMakeCache:
    """Cache creation logic."""

    def test_make_cache_returns_none_when_disabled(self) -> None:
        config = MCPServerConfig(
            name="no-cache",
            transport="stdio",
            command="echo",
            result_cache_max_size=0,
        )
        client = MCPClient(config)
        cache = MCPToolFactory._make_cache(client)
        assert cache is None

    def test_make_cache_returns_cache_when_enabled(self) -> None:
        config = MCPServerConfig(
            name="cached",
            transport="stdio",
            command="echo",
            result_cache_max_size=128,
            result_cache_ttl_seconds=30.0,
        )
        client = MCPClient(config)
        cache = MCPToolFactory._make_cache(client)
        assert cache is not None
