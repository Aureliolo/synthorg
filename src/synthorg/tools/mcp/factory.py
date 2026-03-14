"""MCP tool factory — discovers and creates bridge tools.

Connects to all enabled MCP servers in parallel, discovers their
tools, and wraps each as an ``MCPBridgeTool``.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CLIENT_DISCONNECT_FAILED,
    MCP_FACTORY_CLEANUP,
    MCP_FACTORY_COMPLETE,
    MCP_FACTORY_REUSE_REJECTED,
    MCP_FACTORY_SERVER_SKIPPED,
    MCP_FACTORY_START,
)
from synthorg.tools.mcp.bridge_tool import MCPBridgeTool
from synthorg.tools.mcp.cache import MCPResultCache
from synthorg.tools.mcp.client import MCPClient

if TYPE_CHECKING:
    from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig
    from synthorg.tools.mcp.models import MCPToolInfo

logger = get_logger(__name__)


class MCPToolFactory:
    """Factory that connects to MCP servers and creates bridge tools.

    Manages the lifecycle of MCP clients and creates
    ``MCPBridgeTool`` instances for all discovered tools.

    Args:
        config: MCP bridge configuration.
    """

    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._clients: list[MCPClient] = []
        self._created = False

    async def create_tools(self) -> tuple[MCPBridgeTool, ...]:
        """Connect to all enabled servers and create bridge tools.

        Uses ``asyncio.TaskGroup`` for parallel server connections.
        Disabled servers are skipped with a log message.

        Returns:
            Tuple of all discovered and wrapped bridge tools.

        Raises:
            RuntimeError: If called more than once.
            MCPConnectionError: If a server connection fails.
            MCPDiscoveryError: If tool discovery fails.
        """
        if self._created:
            msg = "create_tools() must not be called more than once"
            logger.warning(MCP_FACTORY_REUSE_REJECTED, reason=msg)
            raise RuntimeError(msg)
        self._created = True

        enabled = [s for s in self._config.servers if s.enabled]
        skipped = len(self._config.servers) - len(enabled)

        logger.info(
            MCP_FACTORY_START,
            total_servers=len(self._config.servers),
            enabled_servers=len(enabled),
            skipped_servers=skipped,
        )

        for server in self._config.servers:
            if not server.enabled:
                logger.info(
                    MCP_FACTORY_SERVER_SKIPPED,
                    server=server.name,
                    reason="disabled",
                )

        if not enabled:
            logger.info(MCP_FACTORY_COMPLETE, tool_count=0)
            return ()

        results = await self._connect_all(enabled)
        bridge_tools = self._build_bridge_tools(results)

        logger.info(MCP_FACTORY_COMPLETE, tool_count=len(bridge_tools))
        return bridge_tools

    async def _connect_all(
        self,
        servers: list[MCPServerConfig],
    ) -> list[tuple[MCPClient, tuple[MCPToolInfo, ...]]]:
        """Connect to servers in parallel and collect results.

        Args:
            servers: Enabled server configurations.

        Returns:
            List of (client, tools) tuples.
        """
        tasks: list[asyncio.Task[tuple[MCPClient, tuple[MCPToolInfo, ...]]]] = []
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(
                        self._connect_and_discover(cfg),
                    )
                    for cfg in servers
                ]
        except BaseException:
            # Clean up any clients that connected before the failure
            logger.warning(
                MCP_FACTORY_CLEANUP,
                reason="partial failure during parallel connect",
            )
            for task in tasks:
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc is None:
                        client, _ = task.result()
                        with contextlib.suppress(Exception):
                            await client.disconnect()
            raise

        results: list[tuple[MCPClient, tuple[MCPToolInfo, ...]]] = []
        for task in tasks:
            client, tools = task.result()
            self._clients.append(client)
            results.append((client, tools))
        return results

    async def shutdown(self) -> None:
        """Disconnect all managed MCP clients."""
        try:
            for client in self._clients:
                try:
                    await client.disconnect()
                except Exception as exc:
                    logger.warning(
                        MCP_CLIENT_DISCONNECT_FAILED,
                        server=client.server_name,
                        error=f"disconnect failed: {exc}",
                    )
        finally:
            self._clients.clear()

    # ── Private helpers ──────────────────────────────────────────

    @staticmethod
    async def _connect_and_discover(
        config: MCPServerConfig,
    ) -> tuple[MCPClient, tuple[MCPToolInfo, ...]]:
        """Connect to a server and discover its tools.

        Disconnects the client if discovery fails after a
        successful connection.

        Args:
            config: Server configuration.

        Returns:
            Tuple of (connected client, discovered tools).
        """
        client = MCPClient(config)
        await client.connect()
        try:
            tools = await client.list_tools()
        except BaseException:
            await client.disconnect()
            raise
        return (client, tools)

    def _build_bridge_tools(
        self,
        results: list[tuple[MCPClient, tuple[MCPToolInfo, ...]]],
    ) -> tuple[MCPBridgeTool, ...]:
        """Create bridge tools from connected clients.

        Args:
            results: List of (client, tools) pairs.

        Returns:
            Tuple of ``MCPBridgeTool`` instances.
        """
        all_tools: list[MCPBridgeTool] = []
        for client, tools in results:
            cache = self._make_cache(client)
            for tool_info in tools:
                bridge = MCPBridgeTool(
                    tool_info=tool_info,
                    client=client,
                    cache=cache,
                )
                all_tools.append(bridge)
        return tuple(all_tools)

    @staticmethod
    def _make_cache(
        client: MCPClient,
    ) -> MCPResultCache | None:
        """Create a result cache if configured.

        Args:
            client: Connected MCP client.

        Returns:
            ``MCPResultCache`` or ``None`` if disabled.
        """
        config = client.config
        if config.result_cache_max_size <= 0:
            return None
        return MCPResultCache(
            max_size=config.result_cache_max_size,
            ttl_seconds=config.result_cache_ttl_seconds,
        )
