"""Tool invoker — validates and executes tool calls.

Bridges LLM ``ToolCall`` objects with concrete ``BaseTool.execute``
methods.  Never propagates exceptions — always returns a ``ToolResult``.

Note:
    ``BaseException`` subclasses (``KeyboardInterrupt``, ``SystemExit``,
    ``asyncio.CancelledError``) are NOT caught and will propagate
    normally.  Non-recoverable errors (``MemoryError``,
    ``RecursionError``) are also re-raised.
"""

from typing import TYPE_CHECKING

import jsonschema
from referencing import Registry as JsonSchemaRegistry
from referencing import Resource
from referencing.exceptions import NoSuchResource

from ai_company.observability import get_logger
from ai_company.observability.events import (
    TOOL_INVOKE_EXECUTION_ERROR,
    TOOL_INVOKE_NOT_FOUND,
    TOOL_INVOKE_PARAMETER_ERROR,
    TOOL_INVOKE_SCHEMA_ERROR,
    TOOL_INVOKE_START,
    TOOL_INVOKE_SUCCESS,
    TOOL_INVOKE_TOOL_ERROR,
)
from ai_company.providers.models import ToolCall, ToolResult

from .errors import ToolExecutionError, ToolNotFoundError, ToolParameterError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .registry import ToolRegistry

logger = get_logger(__name__)


def _no_remote_retrieve(uri: str) -> Resource:
    """Block remote ``$ref`` resolution to prevent SSRF."""
    raise NoSuchResource(uri)


_SAFE_REGISTRY: JsonSchemaRegistry = JsonSchemaRegistry(  # type: ignore[call-arg]
    retrieve=_no_remote_retrieve,
)


class ToolInvoker:
    """Validates parameters and executes tool calls against a registry.

    All errors are caught and returned as ``ToolResult(is_error=True)``
    — the invoker never raises on tool failures.

    Examples:
        Invoke a single tool call::

            invoker = ToolInvoker(registry)
            result = await invoker.invoke(tool_call)

        Invoke multiple tool calls sequentially::

            results = await invoker.invoke_all(tool_calls)
    """

    def __init__(self, registry: ToolRegistry) -> None:
        """Initialize with a tool registry.

        Args:
            registry: Registry to look up tools from.
        """
        self._registry = registry

    async def invoke(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call.

        Steps:
            1. Look up the tool in the registry.
            2. Validate arguments against the tool's JSON Schema (if any).
            3. Call ``tool.execute(arguments=...)``.
            4. Return a ``ToolResult`` with the output.

        Any error at any step produces a ``ToolResult(is_error=True)``
        rather than propagating the exception.

        Args:
            tool_call: The tool call from the LLM.

        Returns:
            A ``ToolResult`` with the tool's output or error message.
        """
        logger.info(
            TOOL_INVOKE_START,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
        )

        try:
            tool = self._registry.get(tool_call.name)
        except ToolNotFoundError as exc:
            logger.warning(
                TOOL_INVOKE_NOT_FOUND,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(exc),
                is_error=True,
            )

        schema = tool.parameters_schema
        if schema is not None:
            try:
                jsonschema.validate(
                    instance=dict(tool_call.arguments),
                    schema=schema,
                    registry=_SAFE_REGISTRY,
                )
            except jsonschema.SchemaError as exc:
                logger.exception(
                    TOOL_INVOKE_SCHEMA_ERROR,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    error=exc.message,
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=(
                        f"Tool {tool_call.name!r} has an invalid "
                        f"parameter schema: {exc.message}"
                    ),
                    is_error=True,
                )
            except jsonschema.ValidationError as exc:
                logger.warning(
                    TOOL_INVOKE_PARAMETER_ERROR,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    error=exc.message,
                )
                param_err = ToolParameterError(
                    exc.message,
                    context={"tool": tool_call.name},
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=str(param_err),
                    is_error=True,
                )

        try:
            result = await tool.execute(arguments=dict(tool_call.arguments))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_msg = str(exc) or f"{type(exc).__name__} (no message)"
            logger.exception(
                TOOL_INVOKE_EXECUTION_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error=error_msg,
            )
            exec_err = ToolExecutionError(
                error_msg,
                context={"tool": tool_call.name},
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(exec_err),
                is_error=True,
            )

        if result.is_error:
            logger.warning(
                TOOL_INVOKE_TOOL_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=result.content,
            )
        else:
            logger.info(
                TOOL_INVOKE_SUCCESS,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=result.content,
            is_error=result.is_error,
        )

    async def invoke_all(
        self,
        tool_calls: Iterable[ToolCall],
    ) -> tuple[ToolResult, ...]:
        """Execute multiple tool calls sequentially.

        All calls are executed regardless of individual failures;
        errors are captured in each ``ToolResult``.

        Note:
            Calls are executed one at a time in order.  Parallel
            execution is left for a future iteration.

        Args:
            tool_calls: Tool calls to execute in order.

        Returns:
            Tuple of results in the same order as the input.
        """
        return tuple([await self.invoke(call) for call in tool_calls])
