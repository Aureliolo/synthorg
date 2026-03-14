"""Handler protocol, adapter, and registration (see Communication design page)."""

import inspect
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.communication import COMM_HANDLER_INVALID

logger = get_logger(__name__)


@runtime_checkable
class MessageHandler(Protocol):
    """Protocol for objects that can handle incoming messages."""

    async def handle(self, message: Message) -> None:
        """Process a single message.

        Args:
            message: The message to handle.
        """
        ...


MessageHandlerFunc = Callable[[Message], Awaitable[None]]
"""Type alias for bare async functions usable as message handlers."""


class FunctionHandler:
    """Adapter wrapping a bare async function as a :class:`MessageHandler`.

    Args:
        func: The async coroutine function to wrap.

    Raises:
        TypeError: If *func* is not an async coroutine function.
    """

    __slots__ = ("_func",)

    def __init__(self, func: MessageHandlerFunc) -> None:
        if not inspect.iscoroutinefunction(func):
            msg = (
                "Handler function must be async (coroutine function), "
                f"got {type(func).__name__}"
            )
            logger.warning(
                COMM_HANDLER_INVALID,
                func_type=type(func).__name__,
                func_name=getattr(func, "__name__", "<unknown>"),
            )
            raise TypeError(msg)
        self._func = func

    async def handle(self, message: Message) -> None:
        """Delegate to the wrapped function.

        Args:
            message: The message to handle.
        """
        await self._func(message)


_PRIORITY_ORDER: MappingProxyType[MessagePriority, int] = MappingProxyType(
    {
        MessagePriority.LOW: 0,
        MessagePriority.NORMAL: 1,
        MessagePriority.HIGH: 2,
        MessagePriority.URGENT: 3,
    }
)


def priority_at_least(
    value: MessagePriority,
    minimum: MessagePriority,
) -> bool:
    """Check whether *value* is at least as high as *minimum*.

    Args:
        value: The priority to check.
        minimum: The minimum acceptable priority.

    Returns:
        True if *value* >= *minimum* in priority ordering.
    """
    return _PRIORITY_ORDER[value] >= _PRIORITY_ORDER[minimum]


class HandlerRegistration(BaseModel):
    """Immutable record binding a handler to its filter criteria.

    Attributes:
        handler_id: Unique registration identifier.
        handler: The handler instance (excluded from serialization).
        message_types: Types to match; empty means match all.
        min_priority: Minimum message priority to accept.
        name: Human-readable label for debugging.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    handler_id: NotBlankStr = Field(default_factory=lambda: str(uuid4()))
    handler: MessageHandler = Field(exclude=True)
    message_types: frozenset[MessageType] = Field(default=frozenset())
    min_priority: MessagePriority = Field(default=MessagePriority.LOW)
    name: NotBlankStr = Field(default="unnamed")
