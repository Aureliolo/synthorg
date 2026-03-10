"""Tool error hierarchy.

All tool errors carry an immutable context mapping for structured
metadata.  Unlike provider errors, tool errors have no ``is_retryable``
flag — retry decisions are made at higher layers.
"""

from types import MappingProxyType
from typing import Any


class ToolError(Exception):
    """Base exception for all tool-layer errors.

    Attributes:
        message: Human-readable error description.
        context: Immutable metadata about the error (tool name, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a tool error.

        Args:
            message: Human-readable error description.
            context: Arbitrary metadata about the error. Stored as an
                immutable mapping; defaults to empty if not provided.
        """
        self.message = message
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            dict(context) if context else {},
        )
        super().__init__(message)

    def __str__(self) -> str:
        """Format error with optional context metadata."""
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{self.message} ({ctx})"
        return self.message


class ToolNotFoundError(ToolError):
    """Requested tool is not registered in the registry."""


class ToolParameterError(ToolError):
    """Tool parameters failed schema validation."""


class ToolExecutionError(ToolError):
    """Tool execution raised an unexpected error."""


class ToolPermissionDeniedError(ToolError):
    """Tool invocation blocked by the permission checker."""
