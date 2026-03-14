"""MCP bridge tool — wraps an MCP server tool as a ``BaseTool``.

Each ``MCPBridgeTool`` instance represents a single tool discovered
from an MCP server, bridging MCP protocol calls into the internal
tool system.
"""

from typing import TYPE_CHECKING, Any

from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CACHE_HIT,
    MCP_CACHE_MISS,
    MCP_CACHE_STORE_FAILED,
    MCP_INVOKE_FAILED,
    MCP_INVOKE_START,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.mcp.errors import MCPError
from synthorg.tools.mcp.result_mapper import map_call_tool_result

if TYPE_CHECKING:
    from synthorg.tools.mcp.cache import MCPResultCache
    from synthorg.tools.mcp.client import MCPClient
    from synthorg.tools.mcp.models import MCPToolInfo

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

    @property
    def tool_info(self) -> MCPToolInfo:
        """The underlying MCP tool metadata."""
        return self._tool_info

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
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

        result = await self._invoke(arguments)
        self._store_in_cache(arguments, result)
        return result

    def _check_cache(
        self,
        arguments: dict[str, Any],
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
        arguments: dict[str, Any],
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
                error=str(exc),
            )
            return ToolExecutionResult(
                content=str(exc),
                is_error=True,
            )
        return map_call_tool_result(raw)

    def _store_in_cache(
        self,
        arguments: dict[str, Any],
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
