"""Communication error hierarchy (DESIGN_SPEC Section 5).

All communication errors carry an immutable context mapping for
structured metadata, following the same pattern as ``ToolError``.
"""

from types import MappingProxyType
from typing import Any


class CommunicationError(Exception):
    """Base exception for all communication-layer errors.

    Attributes:
        message: Human-readable error description.
        context: Immutable metadata about the error.
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a communication error.

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


class ChannelNotFoundError(CommunicationError):
    """Requested channel does not exist."""


class ChannelAlreadyExistsError(CommunicationError):
    """Channel with the given name already exists."""


class NotSubscribedError(CommunicationError):
    """Agent is not subscribed to the specified channel."""


class MessageBusNotRunningError(CommunicationError):
    """Operation attempted on a message bus that is not running."""


class MessageBusAlreadyRunningError(CommunicationError):
    """start() called on a message bus that is already running."""
