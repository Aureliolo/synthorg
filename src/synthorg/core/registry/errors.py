"""Errors raised by :class:`synthorg.core.registry.StrategyRegistry`."""

import copy
from types import MappingProxyType
from typing import Any


class StrategyFactoryError(LookupError):
    """Base class for strategy registry lookup failures."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Store *message* and an immutable *context* mapping."""
        self.message = message
        # Deep-copy to insulate the stored context from later mutation of
        # nested values supplied by the caller (e.g. a list of names).
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            copy.deepcopy(context) if context else {},
        )
        super().__init__(message)

    def __str__(self) -> str:
        """Render with context for log output."""
        if not self.context:
            return self.message
        ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({ctx})"


class StrategyFactoryNotFoundError(StrategyFactoryError):
    """No factory registered for the requested discriminator value."""
