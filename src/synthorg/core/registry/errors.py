"""Errors raised by :class:`synthorg.core.registry.StrategyRegistry`."""

import copy
from types import MappingProxyType
from typing import Any, ClassVar, override

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class StrategyFactoryError(DomainError):
    """Base class for strategy registry lookup failures."""

    default_message: ClassVar[str] = "Strategy factory error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Store *message* and an immutable *context* mapping."""
        self.message = message
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            copy.deepcopy(context) if context else {},
        )
        super().__init__(message)

    @override
    def __str__(self) -> str:
        """Render with context for log output.

        Returns:
            The message alone when no context is attached, otherwise the
            message followed by the context key/value pairs.
        """
        if not self.context:
            return self.message
        ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({ctx})"


class StrategyFactoryNotFoundError(StrategyFactoryError):
    """No factory registered for the requested discriminator value."""

    default_message: ClassVar[str] = "Strategy factory not registered"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404
