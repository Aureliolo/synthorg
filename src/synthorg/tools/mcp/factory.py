"""MCP tool factory -- discovers and creates bridge tools.

Connects to all enabled MCP servers in parallel, discovers their
tools, and wraps each as an ``MCPBridgeTool``.
"""

import asyncio

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_CLIENT_DISCONNECT_FAILED,
    MCP_FACTORY_COMPLETE,
    MCP_FACTORY_REUSE_REJECTED,
    MCP_FACTORY_SERVER_FAILED,
    MCP_FACTORY_SERVER_SKIPPED,
    MCP_FACTORY_START,
)
from synthorg.tools.mcp.bridge_tool import MCPBridgeTool
from synthorg.tools.mcp.cache import MCPResultCache
from synthorg.tools.mcp.client import MCPClient, MCPCredentialResolver
from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig
from synthorg.tools.mcp.errors import MCPConnectionError, MCPDiscoveryError
from synthorg.tools.mcp.models import MCPServerStatus, MCPToolInfo
from synthorg.tools.mcp.sandbox import MCPSandboxConfig

logger = get_logger(__name__)


class MCPToolFactory:
    """Factory that connects to MCP servers and creates bridge tools.

    Manages the lifecycle of MCP clients and creates
    ``MCPBridgeTool`` instances for all discovered tools.

    Args:
        config: MCP bridge configuration.
    """

    def __init__(
        self,
        config: MCPConfig,
        *,
        credential_source: MCPCredentialResolver | None = None,
        sandbox: MCPSandboxConfig | None = None,
    ) -> None:
        self._config = config
        self._credential_source = credential_source
        self._sandbox = sandbox
        self._clients: list[MCPClient] = []
        self._server_statuses: list[MCPServerStatus] = []
        self._created = False

    @property
    def server_statuses(self) -> tuple[MCPServerStatus, ...]:
        """Per-server connect outcomes from the last ``create_tools`` pass."""
        return tuple(self._server_statuses)

    async def ping_servers(self) -> tuple[MCPServerStatus, ...]:
        """Live-ping each connected server, reporting fresh per-server liveness.

        Unlike :attr:`server_statuses` (the last connect outcome) this issues a
        real ``list_tools`` over each existing session, so a server whose child
        died after boot surfaces as unhealthy without re-spawning it. Pings run
        concurrently and never raise: a failing server is reported, not thrown.

        Returns:
            A per-server :class:`MCPServerStatus` snapshot taken just now.
        """
        pings = await asyncio.gather(
            *(self._ping_one(client) for client in self._clients),
            return_exceptions=False,
        )
        return tuple(pings)

    async def _ping_one(self, client: MCPClient) -> MCPServerStatus:
        """Ping a single client via ``list_tools``, mapping failure to a status.

        Returns:
            A fresh :class:`MCPServerStatus` for the pinged client.
        """
        try:
            tools = await client.list_tools()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return MCPServerStatus(
                name=client.server_name,
                connected=False,
                error=safe_error_description(exc),
            )
        return MCPServerStatus(
            name=client.server_name,
            connected=True,
            tool_count=len(tools),
        )

    async def create_tools(self) -> tuple[MCPBridgeTool, ...]:
        """Connect to all enabled servers and create bridge tools.

        Servers connect in parallel with per-server failure isolation: a
        server that fails to connect or discover is logged and dropped, and
        the tools of the servers that succeeded are still returned. Disabled
        servers are skipped with a log message.

        Returns:
            Tuple of discovered and wrapped bridge tools (empty if every
            enabled server failed).

        Raises:
            RuntimeError: If called more than once.
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
        """Connect to servers in parallel, isolating per-server failures.

        Failure isolation is the point: a single broken server (a missing
        credential, an npm outage, a crashed child) must NOT take down every
        other server's tools. ``gather(return_exceptions=True)`` lets each
        connect resolve independently; a failed one is logged with its server
        name and dropped, and only the servers that connected are returned.
        Interpreter-critical exceptions still propagate via
        :func:`reraise_critical`.

        Args:
            servers: Enabled server configurations.

        Returns:
            List of (client, tools) tuples for the servers that connected.
        """
        outcomes = await asyncio.gather(
            *(self._connect_and_discover(cfg) for cfg in servers),
            return_exceptions=True,
        )
        results: list[tuple[MCPClient, tuple[MCPToolInfo, ...]]] = []
        for cfg, outcome in zip(servers, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                reraise_critical(outcome)
                self._record_failure(cfg, outcome)
                continue
            client, tools = outcome
            self._clients.append(client)
            self._server_statuses.append(
                MCPServerStatus(
                    name=cfg.name,
                    connected=True,
                    tool_count=len(tools),
                ),
            )
            results.append((client, tools))
        return results

    def _record_failure(
        self,
        cfg: MCPServerConfig,
        outcome: BaseException,
    ) -> None:
        """Log and record a dropped server, escalating enabled-but-broken.

        A typed connect/discovery error on an explicitly-enabled server is an
        operator misconfiguration (bad package, unresolvable connection), so it
        logs at ERROR to match the rest of the wiring layer; an unexpected
        transient stays at WARNING.
        """
        description = safe_error_description(outcome)
        is_misconfig = isinstance(outcome, MCPConnectionError | MCPDiscoveryError)
        log = logger.error if is_misconfig else logger.warning
        log(
            MCP_FACTORY_SERVER_FAILED,
            server=cfg.name,
            reason="connect/discover failed; server dropped",
            error_type=type(outcome).__name__,
            error=description,
        )
        self._server_statuses.append(
            MCPServerStatus(name=cfg.name, connected=False, error=description),
        )

    async def shutdown(self) -> None:
        """Disconnect all managed MCP clients concurrently.

        Fan out like ``_connect_all`` so one hung server's bounded
        disconnect timeout cannot serialise behind the others' (turning an
        N-server shutdown into N x the per-client timeout).
        """
        try:
            await asyncio.gather(
                *(self._disconnect_one(client) for client in self._clients),
                return_exceptions=True,
            )
        finally:
            self._clients.clear()

    async def _disconnect_one(self, client: MCPClient) -> None:
        """Disconnect a single client, logging (not raising) a failure."""
        try:
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MCP_CLIENT_DISCONNECT_FAILED,
                server=client.server_name,
                context="disconnect failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # ── Private helpers ──────────────────────────────────────────

    async def _connect_and_discover(
        self,
        config: MCPServerConfig,
    ) -> tuple[MCPClient, tuple[MCPToolInfo, ...]]:
        """Connect to a server and discover its tools.

        Disconnects the client if discovery fails after a
        successful connection.

        Args:
            config: Server configuration.

        Returns:
            Tuple of (connected client, discovered tools).

        Raises:
            BaseException: Raised when the relevant invariant fails.
        """
        client = MCPClient(
            config,
            credential_source=self._credential_source,
            sandbox=self._sandbox,
        )
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
