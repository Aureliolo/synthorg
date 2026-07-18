"""MCP bridge tool -- wraps an MCP server tool as a ``BaseTool``.

Each ``MCPBridgeTool`` instance represents a single tool discovered
from an MCP server, bridging MCP protocol calls into the internal
tool system.
"""

import asyncio
from collections.abc import Hashable
from typing import override

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_CACHE_HIT,
    MCP_CACHE_INFLIGHT_WAIT,
    MCP_CACHE_MISS,
    MCP_CACHE_STORE_FAILED,
    MCP_INVOKE_FAILED,
    MCP_INVOKE_START,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.mcp.cache import MCPResultCache, make_arguments_hashable
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.errors import MCPError
from synthorg.tools.mcp.models import MCPToolInfo
from synthorg.tools.mcp.result_mapper import map_call_tool_result

logger = get_logger(__name__)


class MCPBridgeTool(BaseTool):
    """Bridge between an MCP server tool and the internal tool system.

    Constructs a ``BaseTool`` whose ``execute`` delegates to an MCP
    server via ``MCPClient``. An optional ``MCPResultCache`` avoids
    redundant remote calls for identical invocations.

    Args:
        tool_info: Discovered MCP tool metadata.
        client: Connected MCP client for the server.
        cache: Optional result cache.
    """

    def __init__(
        self,
        *,
        tool_info: MCPToolInfo,
        client: MCPClient,
        cache: MCPResultCache | None = None,
    ) -> None:
        super().__init__(
            name=f"mcp_{tool_info.server_name}_{tool_info.name}",
            description=tool_info.description,
            parameters_schema=tool_info.input_schema or None,
            category=ToolCategory.MCP,
        )
        self._client = client
        self._tool_info = tool_info
        self._cache = cache
        # Single-flight coalescing: concurrent identical invocations share one
        # in-flight call rather than each hitting the server. Critically this
        # stops a mutating tool (create_issue, send_message) from executing
        # twice when two agents race the same call before either populates the
        # cache -- the same double-execution the client's heal path guards.
        self._inflight: dict[Hashable, asyncio.Future[ToolExecutionResult]] = {}
        self._inflight_lock = asyncio.Lock()

    @property
    def tool_info(self) -> MCPToolInfo:
        """The underlying MCP tool metadata."""
        return self._tool_info

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute the MCP tool via the client.

        Checks the cache first (if available). On cache miss,
        invokes the remote tool and stores the result.

        Args:
            arguments: Tool invocation arguments.

        Returns:
            Mapped ``ToolExecutionResult``.
        """
        cached = self._check_cache(arguments)
        if cached is not None:
            return cached

        try:
            key = make_arguments_hashable(arguments)
        except TypeError:
            # Unhashable arguments cannot be coalesced (or cached); invoke
            # directly, matching the cache's own unhashable fallback.
            return await self._invoke(arguments)
        return await self._invoke_single_flight(key, arguments)

    async def _invoke_single_flight(
        self,
        key: Hashable,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Invoke, coalescing a concurrent identical call onto one in-flight run.

        Returns:
            Mapped ``ToolExecutionResult`` (shared with any coalesced waiter).
        """
        my_future: asyncio.Future[ToolExecutionResult] = (
            asyncio.get_running_loop().create_future()
        )
        async with self._inflight_lock:
            inflight = self._inflight.get(key)
            if inflight is None:
                self._inflight[key] = my_future
        if inflight is not None:
            # Another identical call is in flight; share its single result
            # (or its failure) instead of issuing a second server call.
            logger.debug(
                MCP_CACHE_INFLIGHT_WAIT,
                tool_name=self._tool_info.name,
                server=self._tool_info.server_name,
            )
            return await inflight
        try:
            result = await self._invoke(arguments)
        except BaseException as exc:
            # Propagate to coalesced waiters too (incl. cancellation), then
            # re-raise for this caller.
            my_future.set_exception(exc)
            raise
        else:
            self._store_in_cache(arguments, result)
            my_future.set_result(result)
            return result
        finally:
            async with self._inflight_lock:
                self._inflight.pop(key, None)

    def _check_cache(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult | None:
        """Look up the cache, returning the result on hit.

        Args:
            arguments: Tool invocation arguments.

        Returns:
            Cached result or ``None``.
        """
        if self._cache is None:
            return None
        try:
            cached = self._cache.get(
                self._tool_info.name,
                arguments,
            )
        except TypeError:
            logger.debug(
                MCP_CACHE_MISS,
                tool_name=self._tool_info.name,
                server=self._tool_info.server_name,
                reason="unhashable arguments",
            )
            return None
        if cached is not None:
            logger.debug(
                MCP_CACHE_HIT,
                tool_name=self._tool_info.name,
                server=self._tool_info.server_name,
            )
        return cached

    async def _invoke(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Call the remote MCP tool and map the result.

        Args:
            arguments: Tool invocation arguments.

        Returns:
            Mapped ``ToolExecutionResult``.
        """
        logger.debug(
            MCP_INVOKE_START,
            tool=self._tool_info.name,
            server=self._tool_info.server_name,
        )
        try:
            raw = await self._client.call_tool(
                self._tool_info.name,
                arguments,
            )
        except MCPError as exc:
            logger.warning(
                MCP_INVOKE_FAILED,
                tool=self._tool_info.name,
                server=self._tool_info.server_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=safe_error_description(exc),
                is_error=True,
            )
        return map_call_tool_result(raw)

    def _store_in_cache(
        self,
        arguments: dict[str, object],
        result: ToolExecutionResult,
    ) -> None:
        """Store a successful result in the cache.

        Skips caching for error results (to avoid replaying
        transient failures) and unhashable arguments.

        Args:
            arguments: Tool invocation arguments.
            result: The result to cache.
        """
        if self._cache is None or result.is_error:
            return
        try:
            self._cache.put(
                self._tool_info.name,
                arguments,
                result,
            )
        except TypeError:
            logger.debug(
                MCP_CACHE_STORE_FAILED,
                tool_name=self._tool_info.name,
                server=self._tool_info.server_name,
                reason="unhashable arguments",
            )
