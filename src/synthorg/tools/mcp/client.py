"""MCP client — thin async wrapper over the MCP SDK.

Manages a single connection to an MCP server and provides
tool discovery and invocation through the MCP protocol.
"""

import asyncio
import copy
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CLIENT_CONNECTED,
    MCP_CLIENT_CONNECTING,
    MCP_CLIENT_CONNECTION_FAILED,
    MCP_CLIENT_DISCONNECT_FAILED,
    MCP_CLIENT_DISCONNECTED,
    MCP_CLIENT_RECONNECTING,
    MCP_DISCOVERY_COMPLETE,
    MCP_DISCOVERY_FAILED,
    MCP_DISCOVERY_FILTERED,
    MCP_DISCOVERY_START,
    MCP_INVOKE_FAILED,
    MCP_INVOKE_START,
    MCP_INVOKE_SUCCESS,
    MCP_INVOKE_TIMEOUT,
)
from synthorg.tools.mcp.errors import (
    MCPConnectionError,
    MCPDiscoveryError,
    MCPInvocationError,
    MCPTimeoutError,
)
from synthorg.tools.mcp.models import MCPRawResult, MCPToolInfo

if TYPE_CHECKING:
    from synthorg.tools.mcp.config import MCPServerConfig

logger = get_logger(__name__)


class MCPClient:
    """Async client for a single MCP server.

    Wraps the MCP SDK's ``ClientSession`` to provide connection
    management, tool discovery, and tool invocation. A lock
    serializes all session access to prevent interleaving.

    Args:
        config: Server connection configuration.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    @property
    def config(self) -> MCPServerConfig:
        """Server connection configuration (read-only)."""
        return self._config

    @property
    def is_connected(self) -> bool:
        """Whether the client has an active session."""
        return self._session is not None

    @property
    def server_name(self) -> str:
        """Name of the configured server."""
        return self._config.name

    async def connect(self) -> None:
        """Establish a connection to the MCP server.

        Raises:
            MCPConnectionError: If the connection fails.
            RuntimeError: If already connected.
        """
        async with self._lock:
            if self._session is not None:
                msg = f"Already connected to {self._config.name!r}"
                logger.warning(
                    MCP_CLIENT_CONNECTION_FAILED,
                    server=self._config.name,
                    error=msg,
                )
                raise RuntimeError(msg)
            logger.info(
                MCP_CLIENT_CONNECTING,
                server=self._config.name,
                transport=self._config.transport,
            )
            stack = AsyncExitStack()
            await stack.__aenter__()
            try:
                coro = self._connect_with_stack(stack)
                session = await asyncio.wait_for(
                    coro,
                    timeout=self._config.connect_timeout_seconds,
                )
                self._session = session
                self._exit_stack = stack
                logger.info(
                    MCP_CLIENT_CONNECTED,
                    server=self._config.name,
                )
            except TimeoutError as exc:
                await stack.aclose()
                msg = (
                    f"Connection to {self._config.name!r} timed out "
                    f"after {self._config.connect_timeout_seconds}s"
                )
                logger.warning(
                    MCP_CLIENT_CONNECTION_FAILED,
                    server=self._config.name,
                    error=msg,
                )
                raise MCPConnectionError(
                    msg,
                    context={
                        "server": self._config.name,
                        "transport": self._config.transport,
                    },
                ) from exc
            except MCPConnectionError:
                await stack.aclose()
                raise
            except Exception as exc:
                await stack.aclose()
                logger.exception(
                    MCP_CLIENT_CONNECTION_FAILED,
                    server=self._config.name,
                    error=str(exc),
                )
                msg = f"Failed to connect to {self._config.name!r}: {exc}"
                raise MCPConnectionError(
                    msg,
                    context={
                        "server": self._config.name,
                        "transport": self._config.transport,
                    },
                ) from exc
            except BaseException:
                # CancelledError, KeyboardInterrupt — still close the stack
                await stack.aclose()
                raise

    async def _connect_with_stack(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Connect via the appropriate transport and initialize.

        Args:
            stack: Exit stack for resource management.

        Returns:
            Connected and initialized ``ClientSession``.
        """
        if self._config.transport == "stdio":
            session = await self._connect_stdio(stack)
        else:
            session = await self._connect_http(stack)
        await session.initialize()
        return session

    async def disconnect(self) -> None:
        """Close the connection and release resources."""
        async with self._lock:
            if self._exit_stack is not None:
                try:
                    await self._exit_stack.aclose()
                except Exception as exc:
                    logger.warning(
                        MCP_CLIENT_DISCONNECT_FAILED,
                        server=self._config.name,
                        error=str(exc),
                    )
                else:
                    logger.info(
                        MCP_CLIENT_DISCONNECTED,
                        server=self._config.name,
                    )
                finally:
                    self._session = None
                    self._exit_stack = None

    async def list_tools(self) -> tuple[MCPToolInfo, ...]:
        """Discover tools from the connected server.

        Applies ``enabled_tools`` / ``disabled_tools`` filters
        from the server configuration.

        Returns:
            Filtered tuple of discovered tool metadata.

        Raises:
            MCPDiscoveryError: If discovery fails.
        """
        async with self._lock:
            session = self._require_session()
            logger.info(
                MCP_DISCOVERY_START,
                server=self._config.name,
            )
            try:
                result = await session.list_tools()
            except Exception as exc:
                logger.exception(
                    MCP_DISCOVERY_FAILED,
                    server=self._config.name,
                    error=str(exc),
                )
                msg = f"Tool discovery failed for {self._config.name!r}: {exc}"
                raise MCPDiscoveryError(
                    msg,
                    context={"server": self._config.name},
                ) from exc

        tools = tuple(
            MCPToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=(copy.deepcopy(t.inputSchema) if t.inputSchema else {}),
                server_name=self._config.name,
            )
            for t in result.tools
        )

        filtered = self._apply_filters(tools)
        logger.info(
            MCP_DISCOVERY_COMPLETE,
            server=self._config.name,
            total=len(tools),
            after_filter=len(filtered),
        )
        return filtered

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPRawResult:
        """Invoke a tool on the connected server.

        Acquires the session lock to respect MCP's sequential
        protocol constraint. Applies the configured timeout.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Arguments to pass to the tool.

        Returns:
            Raw result from the MCP server.

        Raises:
            MCPTimeoutError: If the invocation times out.
            MCPInvocationError: If the invocation fails.
        """
        logger.debug(
            MCP_INVOKE_START,
            server=self._config.name,
            tool=tool_name,
        )
        async with self._lock:
            session = self._require_session()
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=self._config.timeout_seconds,
                )
            except TimeoutError as exc:
                logger.warning(
                    MCP_INVOKE_TIMEOUT,
                    server=self._config.name,
                    tool=tool_name,
                    timeout=self._config.timeout_seconds,
                )
                msg = f"Tool {tool_name!r} timed out on {self._config.name!r}"
                raise MCPTimeoutError(
                    msg,
                    context={
                        "server": self._config.name,
                        "tool": tool_name,
                        "timeout": self._config.timeout_seconds,
                    },
                ) from exc
            except Exception as exc:
                logger.exception(
                    MCP_INVOKE_FAILED,
                    server=self._config.name,
                    tool=tool_name,
                    error=str(exc),
                )
                msg = f"Tool {tool_name!r} failed on {self._config.name!r}: {exc}"
                raise MCPInvocationError(
                    msg,
                    context={
                        "server": self._config.name,
                        "tool": tool_name,
                    },
                ) from exc

        logger.info(
            MCP_INVOKE_SUCCESS,
            server=self._config.name,
            tool=tool_name,
        )
        return MCPRawResult(
            content=tuple(result.content),
            is_error=result.isError or False,
            structured_content=(
                copy.deepcopy(result.structuredContent)
                if result.structuredContent is not None
                else None
            ),
        )

    async def reconnect(self) -> None:
        """Disconnect and reconnect to the server.

        Raises:
            MCPConnectionError: If the reconnection fails.
        """
        logger.info(
            MCP_CLIENT_RECONNECTING,
            server=self._config.name,
        )
        await self.disconnect()
        await self.connect()

    async def __aenter__(self) -> Self:
        """Enter async context: connect to server."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context: disconnect from server."""
        await self.disconnect()

    # ── Private helpers ──────────────────────────────────────────

    def _require_session(self) -> ClientSession:
        """Return the active session or raise.

        Returns:
            The active ``ClientSession``.

        Raises:
            MCPConnectionError: If not connected.
        """
        if self._session is None:
            msg = f"Not connected to {self._config.name!r}"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            )
        return self._session

    async def _connect_stdio(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Set up a stdio transport connection.

        Args:
            stack: Exit stack for resource management.

        Returns:
            Connected ``ClientSession`` (not yet initialized).
        """
        if self._config.command is None:
            msg = f"Server {self._config.name!r}: stdio transport requires 'command'"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            )
        params = StdioServerParameters(
            command=self._config.command,
            args=list(self._config.args),
            env=(dict(self._config.env) if self._config.env else None),
        )
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(params),
        )
        return await stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )

    async def _connect_http(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Set up a streamable HTTP transport connection.

        Args:
            stack: Exit stack for resource management.

        Returns:
            Connected ``ClientSession`` (not yet initialized).
        """
        if self._config.url is None:
            msg = f"Server {self._config.name!r}: streamable_http requires 'url'"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            )
        read_stream, write_stream, _ = await stack.enter_async_context(
            streamablehttp_client(
                url=self._config.url,
                headers=(dict(self._config.headers) if self._config.headers else None),
            ),
        )
        return await stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )

    def _apply_filters(
        self,
        tools: tuple[MCPToolInfo, ...],
    ) -> tuple[MCPToolInfo, ...]:
        """Apply enabled/disabled tool filters.

        Args:
            tools: All discovered tools.

        Returns:
            Filtered tool tuple.
        """
        result = tools

        if self._config.enabled_tools is not None:
            allowed = set(self._config.enabled_tools)
            before = len(result)
            result = tuple(t for t in result if t.name in allowed)
            if len(result) < before:
                logger.debug(
                    MCP_DISCOVERY_FILTERED,
                    server=self._config.name,
                    filter_type="enabled_tools",
                    before=before,
                    after=len(result),
                )

        if self._config.disabled_tools:
            blocked = set(self._config.disabled_tools)
            before = len(result)
            result = tuple(t for t in result if t.name not in blocked)
            if len(result) < before:
                logger.debug(
                    MCP_DISCOVERY_FILTERED,
                    server=self._config.name,
                    filter_type="disabled_tools",
                    before=before,
                    after=len(result),
                )

        return result
