"""Tool error hierarchy.

All tool errors carry an immutable context mapping for structured
metadata.  Unlike provider errors, tool errors have no ``is_retryable``
flag -- retry decisions are made at higher layers.
"""

import copy
from types import MappingProxyType
from typing import ClassVar, override

from pydantic import JsonValue

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class ToolError(DomainError):
    """Base exception for all tool-layer errors.

    Attributes:
        message: Human-readable error description.
        context: Immutable metadata about the error (tool name, etc.).

    Class Attributes:
        status_code: HTTP 500 default.
        error_code: ``TOOL_ERROR``; subclasses override.
        error_category: ``INTERNAL``.
        retryable: ``False``.
        default_message: Generic 5xx-safe message.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Tool error"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        """Initialize a tool error.

        Args:
            message: Human-readable error description.
            context: Arbitrary metadata about the error. Stored as an
                immutable mapping; defaults to empty if not provided.
        """
        self.message = message
        # Deep-copy so nested mutable values in ``context`` are not
        # shared with the caller after the exception is raised; the
        # ``MappingProxyType`` wrapper also prevents top-level mutation
        # of the attribute itself (CLAUDE.md immutability rule).
        self.context: MappingProxyType[str, JsonValue] = MappingProxyType(
            copy.deepcopy(context) if context else {},
        )
        super().__init__(message)

    @override
    def __str__(self) -> str:
        """Format error with optional context metadata.

        Returns:
            Result of type ``str``.
        """
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{self.message} ({ctx})"
        return self.message


class ToolNotFoundError(ToolError):
    """Requested tool is not registered in the registry."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Tool not found"


class ToolParameterError(ToolError):
    """Tool parameters failed schema validation."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Tool parameter validation failed"


class ToolExecutionError(ToolError):
    """Tool execution raised an unexpected error."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    default_message: ClassVar[str] = "Tool execution failed"


class ToolPermissionDeniedError(ToolError):
    """Tool invocation blocked by the permission checker."""

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PERMISSION_DENIED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Tool invocation not permitted"
