"""MCP tool invocation dispatcher.

Provides ``MCPToolInvoker`` which dispatches MCP tool calls to
registered handler functions, with structured error mapping.
"""

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_SERVER_INVOKE_FAILED,
    MCP_SERVER_INVOKE_START,
    MCP_SERVER_INVOKE_SUCCESS,
)
from synthorg.tools.base import ToolExecutionResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity
    from synthorg.meta.mcp.registry import DomainToolRegistry

logger = get_logger(__name__)


__all__ = ["MCPToolInvoker", "ToolHandler"]


class MCPToolInvoker:
    """Dispatches MCP tool invocations to registered handlers.

    Looks up the handler by the tool's ``handler_key`` in the registry,
    invokes it with ``app_state`` and ``arguments``, and maps exceptions
    to ``ToolExecutionResult`` with ``is_error=True``.

    Args:
        registry: Domain tool registry for handler key lookup.
        handlers: Mapping of handler keys to handler functions.
    """

    def __init__(
        self,
        registry: DomainToolRegistry,
        handlers: Mapping[str, ToolHandler],
    ) -> None:
        self._registry = registry
        self._handlers = handlers

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        app_state: Any,
        actor: AgentIdentity | None = None,
    ) -> ToolExecutionResult:
        """Dispatch a tool invocation to its handler.

        All error conditions (tool not found, handler not found,
        handler exception) are caught, logged, and converted to
        ``ToolExecutionResult`` with ``is_error=True``.  The method
        converts all application-level exceptions into error results
        so callers never see them.  System-critical exceptions
        (``MemoryError``, ``RecursionError``) are re-raised.

        Args:
            tool_name: Name of the MCP tool to invoke.
            arguments: Tool call arguments.
            app_state: Application state for service access.
            actor: Calling agent identity (typically
                ``AgentIdentity``), threaded to the handler for
                destructive-op attribution.  Defaults to ``None``;
                destructive handlers will reject with a
                ``guardrail_violated`` error envelope.

        Returns:
            ``ToolExecutionResult`` with the handler's JSON output
            (on success) or a JSON error object (on failure).
        """
        logger.debug(
            MCP_SERVER_INVOKE_START,
            tool_name=tool_name,
        )

        # Look up tool definition.
        try:
            tool_def = self._registry.get(tool_name)
        except KeyError:
            logger.warning(
                MCP_SERVER_INVOKE_FAILED,
                tool_name=tool_name,
                error="tool not found",
            )
            return ToolExecutionResult(
                content=json.dumps({"error": f"Unknown tool: {tool_name}"}),
                is_error=True,
            )

        # Look up handler.
        handler = self._handlers.get(tool_def.handler_key)
        if handler is None:
            logger.warning(
                MCP_SERVER_INVOKE_FAILED,
                tool_name=tool_name,
                error="handler not found",
            )
            return ToolExecutionResult(
                content=json.dumps({"error": f"No handler for tool: {tool_name}"}),
                is_error=True,
            )

        # Optional typed-args validation (Phase 4 of #1611): when the
        # tool's ``MCPToolDef.args_model`` is set, validate the raw
        # arguments dict against the Pydantic model before dispatch.
        # ``ValidationError`` surfaces as an ``invalid_argument`` error
        # envelope without ever reaching the handler.  Unmigrated tools
        # (``args_model is None``) keep their legacy ``common_args``
        # validation inside the handler body.
        if tool_def.args_model is not None:
            try:
                tool_def.args_model.model_validate(dict(arguments))
            except PydanticValidationError as exc:
                first = exc.errors(include_input=False, include_url=False)
                detail = first[0]["msg"] if first else str(exc)
                logger.warning(
                    MCP_SERVER_INVOKE_FAILED,
                    tool_name=tool_name,
                    error_type="ArgumentValidationError",
                    error=detail,
                )
                return ToolExecutionResult(
                    content=json.dumps(
                        {
                            "status": "error",
                            "error_type": "ArgumentValidationError",
                            "message": detail,
                            "domain_code": "invalid_argument",
                        },
                    ),
                    is_error=True,
                )

        # Invoke handler.  Re-raise MemoryError/RecursionError
        # (system-critical) and let application exceptions map to
        # error results.
        try:
            result = await handler(
                app_state=app_state,
                arguments=deepcopy(arguments),
                actor=actor,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            # SEC-1: safe_error_description avoids leaking secrets that
            # str(exc) would expose (httpx POST bodies, Fernet payloads,
            # OAuth refresh tokens).  exc_info is intentionally omitted
            # for the same reason -- frame locals can carry credentials.
            logger.warning(
                MCP_SERVER_INVOKE_FAILED,
                tool_name=tool_name,
                error_type=error_type,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=json.dumps(
                    {
                        "error": error_type,
                        "tool": tool_name,
                    }
                ),
                is_error=True,
            )

        logger.debug(
            MCP_SERVER_INVOKE_SUCCESS,
            tool_name=tool_name,
        )
        return ToolExecutionResult(content=result)
