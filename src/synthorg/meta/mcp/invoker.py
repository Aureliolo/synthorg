"""MCP tool invocation dispatcher.

Provides ``MCPToolInvoker`` which dispatches MCP tool calls to
registered handler functions, with structured error mapping.
"""

import json
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError as PydanticValidationError

from synthorg.api.boundary import parse_typed
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_SERVER_INVOKE_FAILED,
    MCP_SERVER_INVOKE_START,
    MCP_SERVER_INVOKE_SUCCESS,
)
from synthorg.observability.metrics_hub import record_mcp_handler_outcome
from synthorg.tools.base import ToolExecutionResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity
    from synthorg.meta.mcp.registry import DomainToolRegistry

logger = get_logger(__name__)


__all__ = ["MCPToolInvoker", "ToolHandler"]


def _format_pydantic_error(err: object) -> str:
    """Render a single Pydantic ``errors()`` entry as ``loc: msg``."""
    if not isinstance(err, dict):
        return "<arguments>: invalid"
    loc_raw = err.get("loc", ())
    loc_parts = loc_raw if isinstance(loc_raw, tuple) else ()
    loc = ".".join(str(p) for p in loc_parts) or "<arguments>"
    msg = err.get("msg", "")
    return f"{loc}: {msg}" if isinstance(msg, str) else f"{loc}: invalid"


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
        invocation_start = time.perf_counter()

        # Look up tool definition.
        try:
            tool_def = self._registry.get(tool_name)
        except KeyError:
            logger.warning(
                MCP_SERVER_INVOKE_FAILED,
                tool_name=tool_name,
                error="tool not found",
            )
            record_mcp_handler_outcome(
                tool=tool_name,
                outcome="not_found",
                duration_sec=time.perf_counter() - invocation_start,
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
            record_mcp_handler_outcome(
                tool=tool_name,
                outcome="not_found",
                duration_sec=time.perf_counter() - invocation_start,
            )
            return ToolExecutionResult(
                content=json.dumps({"error": f"No handler for tool: {tool_name}"}),
                is_error=True,
            )

        # When the tool registration carries an ``args_model``,
        # validate the raw dict against it and pass the validated
        # ``model_dump()`` to the handler.  Validation failure surfaces
        # as an ``invalid_argument`` error envelope without ever
        # reaching the handler.  Tools without an ``args_model``
        # receive the deepcopied raw dict, validated by the handler's
        # own ``common_args`` calls.
        handler_arguments: dict[str, Any]
        if tool_def.args_model is not None:
            # Reject non-mapping payloads up front. Otherwise
            # ``model_validate`` would still raise, but only after
            # Pydantic walked an iterable that ``dict(...)`` quietly
            # accepts (e.g. a list of pairs), which is not part of the
            # MCP contract.  An explicit shape check keeps the
            # ``invalid_argument`` envelope as the only escape route.
            # The static type is ``dict[str, Any]``, but the MCP wire
            # surface can in practice deliver any JSON value, so the
            # runtime guard erases the static narrowing via ``cast``.
            raw_arguments = cast("object", arguments)
            if not isinstance(raw_arguments, dict):
                detail = (
                    f"arguments must be a JSON object, got "
                    f"{type(raw_arguments).__name__}"
                )
                logger.warning(
                    MCP_SERVER_INVOKE_FAILED,
                    tool_name=tool_name,
                    error_type="ArgumentValidationError",
                    error=detail,
                )
                record_mcp_handler_outcome(
                    tool=tool_name,
                    outcome="validation_error",
                    duration_sec=time.perf_counter() - invocation_start,
                )
                return ToolExecutionResult(
                    content=json.dumps(
                        {
                            "status": "error",
                            "error_type": "ArgumentValidationError",
                            "message": detail,
                            "domain_code": "invalid_argument",
                            "tool": tool_name,
                        },
                    ),
                    is_error=True,
                )
            # Route through the canonical boundary helper so a
            # malformed args payload emits api.boundary.validation_failed
            # alongside the existing mcp.server.invoke.failed event;
            # the helper does not swallow ValidationError so the
            # invoker's existing translation into the
            # ArgumentValidationError envelope still drives the wire
            # response.
            try:
                validated = parse_typed("mcp.tool", arguments, tool_def.args_model)
            except PydanticValidationError as exc:
                errors = exc.errors(include_input=False, include_url=False)
                detail = (
                    "; ".join(_format_pydantic_error(e) for e in errors)
                    if errors
                    else safe_error_description(exc)
                )
                logger.warning(
                    MCP_SERVER_INVOKE_FAILED,
                    tool_name=tool_name,
                    error_type="ArgumentValidationError",
                    error=detail,
                )
                record_mcp_handler_outcome(
                    tool=tool_name,
                    outcome="validation_error",
                    duration_sec=time.perf_counter() - invocation_start,
                )
                return ToolExecutionResult(
                    content=json.dumps(
                        {
                            "status": "error",
                            "error_type": "ArgumentValidationError",
                            "message": detail,
                            "domain_code": "invalid_argument",
                            "tool": tool_name,
                        },
                    ),
                    is_error=True,
                )
            # Deep-copy the validated dump before dispatch so handlers
            # receive a fresh mutable dict; nested ``dict``/``list``
            # fields from the frozen args model would otherwise be
            # shared with subsequent invocations.  Matches the legacy
            # ``deepcopy(arguments)`` path below.
            handler_arguments = deepcopy(validated.model_dump(mode="python"))
        else:
            handler_arguments = deepcopy(arguments)

        # Invoke handler.  Re-raise MemoryError/RecursionError
        # (system-critical) and let application exceptions map to
        # error results.
        try:
            result = await handler(
                app_state=app_state,
                arguments=handler_arguments,
                actor=actor,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            # safe_error_description avoids leaking secrets that
            # str(exc) would expose (httpx POST bodies, Fernet
            # payloads, OAuth refresh tokens). exc_info is
            # intentionally omitted for the same reason -- frame
            # locals can carry credentials.
            logger.warning(
                MCP_SERVER_INVOKE_FAILED,
                tool_name=tool_name,
                error_type=error_type,
                error=safe_error_description(exc),
            )
            record_mcp_handler_outcome(
                tool=tool_name,
                outcome="error",
                duration_sec=time.perf_counter() - invocation_start,
            )
            return ToolExecutionResult(
                content=json.dumps(
                    {
                        "status": "error",
                        "error_type": error_type,
                        "message": safe_error_description(exc) or error_type,
                        "domain_code": "handler_failure",
                        "tool": tool_name,
                    }
                ),
                is_error=True,
            )

        logger.debug(
            MCP_SERVER_INVOKE_SUCCESS,
            tool_name=tool_name,
        )
        record_mcp_handler_outcome(
            tool=tool_name,
            outcome="success",
            duration_sec=time.perf_counter() - invocation_start,
        )
        return ToolExecutionResult(content=result)
