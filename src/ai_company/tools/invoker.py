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
from ai_company.observability.events.security import (
    SECURITY_INTERCEPTOR_ERROR,
    SECURITY_OUTPUT_SCAN_ERROR,
)
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
    TOOL_OUTPUT_REDACTED,
    TOOL_PERMISSION_DENIED,
    TOOL_SECURITY_DENIED,
    TOOL_SECURITY_ESCALATED,
)
from ai_company.providers.models import ToolCall, ToolResult
from ai_company.security.models import SecurityContext, SecurityVerdictType

from .base import ToolExecutionResult
from .errors import ToolExecutionError, ToolNotFoundError, ToolParameterError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ai_company.providers.models import ToolDefinition
    from ai_company.security.protocol import SecurityInterceptionStrategy

    from .base import BaseTool
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
    """Validate parameters, enforce security policies, and execute tools.

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
        security_interceptor: SecurityInterceptionStrategy | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Initialize with a tool registry and optional checkers.

        Args:
            registry: Registry to look up tools from.
            permission_checker: Optional checker for access-level gating.
                When ``None``, all registered tools are permitted.
            security_interceptor: Optional pre/post-tool security layer.
            agent_id: Agent ID for security context.
            task_id: Task ID for security context.
        """
        self._registry = registry
        self._permission_checker = permission_checker
        self._security_interceptor = security_interceptor
        self._agent_id = agent_id
        self._task_id = task_id

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

    def _build_security_context(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> SecurityContext:
        """Build a ``SecurityContext`` for the given tool call."""
        return SecurityContext(
            tool_name=tool.name,
            tool_category=tool.category,
            action_type=tool.action_type,
            arguments=copy.deepcopy(dict(tool_call.arguments)),
            agent_id=self._agent_id,
            task_id=self._task_id,
        )

    async def _check_security(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Run the security interceptor (if any) before execution.

        Returns ``None`` if allowed, or a ``ToolResult(is_error=True)``
        if denied or escalated.  Exceptions from the interceptor are
        caught and converted to error results (fail-closed).
        """
        if self._security_interceptor is None:
            return None
        context = self._build_security_context(tool, tool_call)
        try:
            verdict = await self._security_interceptor.evaluate_pre_tool(
                context,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SECURITY_INTERCEPTOR_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=(
                    "Security evaluation failed (fail-closed). Tool execution blocked."
                ),
                is_error=True,
            )
        if verdict.verdict == SecurityVerdictType.ALLOW:
            return None
        if verdict.verdict == SecurityVerdictType.ESCALATE:
            logger.warning(
                TOOL_SECURITY_ESCALATED,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                reason=verdict.reason,
                approval_id=verdict.approval_id,
            )
            msg = (
                f"Security escalation: {verdict.reason}. "
                f"Approval required (id={verdict.approval_id})"
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=msg,
                is_error=True,
            )
        # DENY
        logger.warning(
            TOOL_SECURITY_DENIED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            reason=verdict.reason,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Security denied: {verdict.reason}",
            is_error=True,
        )

    async def _scan_output(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        context: SecurityContext,
    ) -> ToolExecutionResult:
        """Scan tool output for sensitive data (if interceptor is set).

        If sensitive data is found and redacted, returns a new
        ``ToolExecutionResult`` with the redacted content.  Exceptions
        from the scanner are caught — the original result is returned
        to avoid destroying valid tool output.
        """
        if self._security_interceptor is None:
            return result
        if result.is_error:
            return result

        try:
            scan_result = await self._security_interceptor.scan_output(
                context,
                result.content,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SECURITY_OUTPUT_SCAN_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            return result

        if scan_result.has_sensitive_data and scan_result.redacted_content is not None:
            logger.warning(
                TOOL_OUTPUT_REDACTED,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                findings=scan_result.findings,
            )
            return ToolExecutionResult(
                content=scan_result.redacted_content,
                is_error=result.is_error,
                metadata={
                    **result.metadata,
                    "output_redacted": True,
                    "redaction_findings": list(scan_result.findings),
                },
            )
        return result

    async def invoke(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call.

        Steps:
            1. Look up the tool in the registry.
            2. Check permissions against the permission checker (if any).
            3. Validate arguments against the tool's JSON Schema (if any).
            4. Run security interceptor pre-tool check (if any).
            5. Call ``tool.execute(arguments=...)``.
            6. Scan tool output for sensitive data (if interceptor is set).
            7. Return a ``ToolResult`` with the output.

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

        security_error = await self._check_security(tool_or_error, tool_call)
        if security_error is not None:
            return security_error

        # Build context once for output scanning (reuse same context).
        security_context = (
            self._build_security_context(tool_or_error, tool_call)
            if self._security_interceptor is not None
            else None
        )

        exec_result = await self._execute_tool(tool_or_error, tool_call)
        if isinstance(exec_result, ToolResult):
            return exec_result

        if security_context is not None:
            exec_result = await self._scan_output(
                tool_call,
                exec_result,
                security_context,
            )

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

    def _safe_deepcopy_args(
        self,
        tool_call: ToolCall,
    ) -> dict[str, object] | ToolResult:
        """Deep-copy tool call arguments for isolation.

        Returns the copied dict on success, or a ``ToolResult`` on
        failure.  Non-recoverable errors propagate after logging.
        """
        try:
            return copy.deepcopy(tool_call.arguments)
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

    async def _execute_tool(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolExecutionResult | ToolResult:
        """Deep-copy arguments for isolation, then execute the tool."""
        safe_args = self._safe_deepcopy_args(tool_call)
        if isinstance(safe_args, ToolResult):
            return safe_args
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

    @staticmethod
    def _raise_fatal_errors(fatal_errors: list[Exception]) -> None:
        """Re-raise collected fatal errors after all tasks complete."""
        if not fatal_errors:
            return
        if len(fatal_errors) == 1:
            raise fatal_errors[0]
        msg = "multiple non-recoverable tool errors"
        raise ExceptionGroup(msg, fatal_errors)

    async def invoke_all(
        self,
        tool_calls: Iterable[ToolCall],
        *,
        max_concurrency: int | None = None,
    ) -> tuple[ToolResult, ...]:
        """Execute multiple tool calls concurrently.

        Args:
            tool_calls: Tool calls to execute.
            max_concurrency: Max concurrent invocations (``>= 1``).

        Returns:
            Tuple of results in the same order as the input.

        Raises:
            ValueError: If *max_concurrency* < 1.
            MemoryError: Re-raised if a single fatal error occurred.
            RecursionError: Re-raised if a single fatal error occurred.
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

        results: dict[int, ToolResult] = {}
        fatal_errors: list[Exception] = []
        semaphore = (
            asyncio.Semaphore(max_concurrency) if max_concurrency is not None else None
        )

        async with asyncio.TaskGroup() as tg:
            for idx, call in enumerate(calls):
                tg.create_task(
                    self._run_guarded(
                        idx,
                        call,
                        results,
                        fatal_errors,
                        semaphore,
                    ),
                )

        logger.info(
            TOOL_INVOKE_ALL_COMPLETE,
            count=len(calls),
            fatal_count=len(fatal_errors),
        )

        self._raise_fatal_errors(fatal_errors)
        return tuple(results[i] for i in range(len(calls)))
