"""Tool invoker — validates and executes tool calls.

Bridges LLM ``ToolCall`` objects with concrete ``BaseTool.execute``
methods.  Recoverable errors are returned as ``ToolResult(is_error=True)``;
non-recoverable errors (``MemoryError``, ``RecursionError``) are logged and
re-raised.  ``BaseException`` subclasses (``KeyboardInterrupt``,
``SystemExit``, ``asyncio.CancelledError``) propagate uncaught.
"""

import asyncio
import copy
from contextlib import nullcontext
from typing import TYPE_CHECKING, Never

import jsonschema
from referencing import Registry as JsonSchemaRegistry
from referencing.exceptions import NoSuchResource

from ai_company.observability import get_logger
from ai_company.observability.events.tool import (
    TOOL_INVOKE_ALL_COMPLETE,
    TOOL_INVOKE_ALL_START,
    TOOL_INVOKE_DEEPCOPY_ERROR,
    TOOL_INVOKE_EXECUTION_ERROR,
    TOOL_INVOKE_NON_RECOVERABLE,
    TOOL_INVOKE_NOT_FOUND,
    TOOL_INVOKE_PARAMETER_ERROR,
    TOOL_INVOKE_SCHEMA_ERROR,
    TOOL_INVOKE_START,
    TOOL_INVOKE_SUCCESS,
    TOOL_INVOKE_TOOL_ERROR,
    TOOL_INVOKE_VALIDATION_UNEXPECTED,
    TOOL_PERMISSION_DENIED,
)
from ai_company.providers.models import ToolCall, ToolResult

from .errors import ToolExecutionError, ToolNotFoundError, ToolParameterError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ai_company.providers.models import ToolDefinition

    from .base import BaseTool, ToolExecutionResult
    from .permissions import ToolPermissionChecker
    from .registry import ToolRegistry

logger = get_logger(__name__)


def _no_remote_retrieve(uri: str) -> Never:
    """Block remote ``$ref`` resolution to prevent SSRF."""
    raise NoSuchResource(uri)


_SAFE_REGISTRY: JsonSchemaRegistry = JsonSchemaRegistry(  # type: ignore[call-arg]
    retrieve=_no_remote_retrieve,
)


class ToolInvoker:
    """Validates parameters and executes tool calls against a registry.

    Recoverable errors are returned as ``ToolResult(is_error=True)``.
    Non-recoverable errors (``MemoryError``, ``RecursionError``) are
    re-raised after logging.

    Examples:
        Invoke a single tool call::

            invoker = ToolInvoker(registry)
            result = await invoker.invoke(tool_call)

        Invoke multiple tool calls concurrently::

            results = await invoker.invoke_all(tool_calls)

        Limit concurrency::

            results = await invoker.invoke_all(tool_calls, max_concurrency=3)
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_checker: ToolPermissionChecker | None = None,
    ) -> None:
        """Initialize with a tool registry and optional permission checker.

        Args:
            registry: Registry to look up tools from.
            permission_checker: Optional checker for access-level gating.
                When ``None``, all registered tools are permitted.
        """
        self._registry = registry
        self._permission_checker = permission_checker

    @property
    def registry(self) -> ToolRegistry:
        """Read-only access to the underlying tool registry."""
        return self._registry

    def get_permitted_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return tool definitions filtered by the permission checker.

        When no permission checker is set, returns all definitions.

        Returns:
            Tuple of permitted tool definitions, sorted by name.
        """
        if self._permission_checker is None:
            return self._registry.to_definitions()
        return self._permission_checker.filter_definitions(self._registry)

    def _check_permission(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Check tool permission.

        Returns ``None`` if permitted, or a ``ToolResult(is_error=True)``
        if denied.
        """
        if self._permission_checker is None:
            return None
        if self._permission_checker.is_permitted(tool.name, tool.category):
            return None
        reason = self._permission_checker.denial_reason(tool.name, tool.category)
        logger.warning(
            TOOL_PERMISSION_DENIED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            reason=reason,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Permission denied: {reason}",
            is_error=True,
        )

    async def invoke(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call.

        Steps:
            1. Look up the tool in the registry.
            2. Check permissions against the permission checker (if any).
            3. Validate arguments against the tool's JSON Schema (if any).
            4. Call ``tool.execute(arguments=...)``.
            5. Return a ``ToolResult`` with the output.

        Recoverable errors produce ``ToolResult(is_error=True)``.
        Non-recoverable errors are re-raised.

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

        tool_or_error = self._lookup_tool(tool_call)
        if isinstance(tool_or_error, ToolResult):
            return tool_or_error

        permission_error = self._check_permission(tool_or_error, tool_call)
        if permission_error is not None:
            return permission_error

        param_error = self._validate_params(tool_or_error, tool_call)
        if param_error is not None:
            return param_error

        exec_result = await self._execute_tool(tool_or_error, tool_call)
        if isinstance(exec_result, ToolResult):
            return exec_result

        return self._build_result(tool_call, exec_result)

    def _lookup_tool(self, tool_call: ToolCall) -> BaseTool | ToolResult:
        """Look up a tool in the registry, returning an error on miss."""
        try:
            return self._registry.get(tool_call.name)
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

    def _validate_params(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Validate tool call arguments against JSON Schema.

        Returns ``None`` on success or a ``ToolResult`` on failure.
        """
        schema = tool.parameters_schema
        if schema is None:
            return None
        try:
            jsonschema.validate(
                instance=dict(tool_call.arguments),
                schema=schema,
                registry=_SAFE_REGISTRY,
            )
        except jsonschema.SchemaError as exc:
            return self._schema_error_result(tool_call, exc.message)
        except jsonschema.ValidationError as exc:
            return self._param_error_result(tool_call, exc.message)
        except (MemoryError, RecursionError) as exc:
            logger.exception(
                TOOL_INVOKE_NON_RECOVERABLE,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        except Exception as exc:
            error_msg = str(exc) or f"{type(exc).__name__} (no message)"
            return self._unexpected_validation_result(tool_call, error_msg)
        return None

    def _schema_error_result(
        self,
        tool_call: ToolCall,
        error_msg: str,
    ) -> ToolResult:
        """Build an error result for an invalid tool schema."""
        logger.error(
            TOOL_INVOKE_SCHEMA_ERROR,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            error=error_msg,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=(
                f"Tool {tool_call.name!r} has an invalid parameter schema: {error_msg}"
            ),
            is_error=True,
        )

    def _param_error_result(
        self,
        tool_call: ToolCall,
        error_msg: str,
    ) -> ToolResult:
        """Build an error result for failed parameter validation."""
        logger.warning(
            TOOL_INVOKE_PARAMETER_ERROR,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            error=error_msg,
        )
        param_err = ToolParameterError(
            error_msg,
            context={"tool": tool_call.name},
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=str(param_err),
            is_error=True,
        )

    def _unexpected_validation_result(
        self,
        tool_call: ToolCall,
        error_msg: str,
    ) -> ToolResult:
        """Build an error result for unexpected validation failures."""
        logger.exception(
            TOOL_INVOKE_VALIDATION_UNEXPECTED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            error=error_msg,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=(
                f"Tool {tool_call.name!r} parameter validation failed: {error_msg}"
            ),
            is_error=True,
        )

    async def _execute_tool(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolExecutionResult | ToolResult:
        """Deep-copy arguments for isolation, then execute the tool.

        Copy failures and execution errors are caught and returned as
        ``ToolResult(is_error=True)``.  Non-recoverable errors
        (``MemoryError``, ``RecursionError``) propagate after logging.
        """
        try:
            safe_args = copy.deepcopy(tool_call.arguments)
        except (MemoryError, RecursionError) as exc:
            logger.exception(
                TOOL_INVOKE_NON_RECOVERABLE,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        except Exception as exc:
            error_msg = str(exc) or f"{type(exc).__name__} (no message)"
            logger.exception(
                TOOL_INVOKE_DEEPCOPY_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error=f"Failed to deep-copy arguments: {error_msg}",
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=(
                    f"Tool {tool_call.name!r} arguments could not be "
                    f"safely copied: {error_msg}"
                ),
                is_error=True,
            )
        try:
            return await tool.execute(arguments=safe_args)
        except (MemoryError, RecursionError) as exc:
            logger.exception(
                TOOL_INVOKE_NON_RECOVERABLE,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error=f"{type(exc).__name__}: {exc}",
            )
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

    def _build_result(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
    ) -> ToolResult:
        """Map a successful execution result to a ``ToolResult``."""
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

    async def _run_guarded(
        self,
        index: int,
        tool_call: ToolCall,
        results: dict[int, ToolResult],
        fatal_errors: list[Exception],
        semaphore: asyncio.Semaphore | None,
    ) -> None:
        """Execute a single tool call, storing fatal errors instead of raising.

        This wrapper ensures that ``MemoryError`` / ``RecursionError`` do not
        cancel sibling tasks inside a ``TaskGroup``.  ``BaseException``
        subclasses (``KeyboardInterrupt``, ``CancelledError``) are not
        intercepted and will cancel the group normally.
        """
        try:
            ctx = semaphore if semaphore is not None else nullcontext()
            async with ctx:
                results[index] = await self.invoke(tool_call)
        except (MemoryError, RecursionError) as exc:
            fatal_errors.append(exc)

    async def invoke_all(
        self,
        tool_calls: Iterable[ToolCall],
        *,
        max_concurrency: int | None = None,
    ) -> tuple[ToolResult, ...]:
        """Execute multiple tool calls concurrently.

        Calls continue through recoverable failures; non-recoverable
        errors (``MemoryError``, ``RecursionError``) are collected and
        re-raised after all tasks complete.

        Args:
            tool_calls: Tool calls to execute.
            max_concurrency: Maximum number of concurrent invocations.
                ``None`` (default) means unbounded.  Must be ``>= 1``
                if provided.

        Returns:
            Tuple of results in the same order as the input.

        Raises:
            ValueError: If ``max_concurrency`` is less than 1.
            MemoryError: Re-raised if it was the sole fatal error.
            RecursionError: Re-raised if it was the sole fatal error.
            ExceptionGroup: If multiple fatal errors occurred.
        """
        if max_concurrency is not None and max_concurrency < 1:
            msg = f"max_concurrency must be >= 1, got {max_concurrency}"
            raise ValueError(msg)

        calls = list(tool_calls)
        if not calls:
            return ()

        logger.info(
            TOOL_INVOKE_ALL_START,
            count=len(calls),
            max_concurrency=max_concurrency,
        )

        # SAFETY: Both ``results`` and ``fatal_errors`` are mutated by
        # concurrent tasks.  This is safe because asyncio runs tasks on
        # a single thread — dict assignment and list.append() never race.
        results: dict[int, ToolResult] = {}
        fatal_errors: list[Exception] = []
        semaphore = (
            asyncio.Semaphore(max_concurrency) if max_concurrency is not None else None
        )

        async with asyncio.TaskGroup() as tg:
            for idx, call in enumerate(calls):
                tg.create_task(
                    self._run_guarded(idx, call, results, fatal_errors, semaphore),
                )

        logger.info(
            TOOL_INVOKE_ALL_COMPLETE,
            count=len(calls),
            fatal_count=len(fatal_errors),
        )

        if fatal_errors:
            if len(fatal_errors) == 1:
                raise fatal_errors[0]
            msg = "multiple non-recoverable tool errors"
            raise ExceptionGroup(msg, fatal_errors)

        return tuple(results[i] for i in range(len(calls)))
