"""Message dispatcher -- routes incoming messages to registered handlers.

See the Communication design page.
"""

import asyncio
import inspect
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.handler import (
    FunctionHandler,
    HandlerRegistration,
    MessageHandler,
    MessageHandlerFunc,
    priority_at_least,
)
from synthorg.communication.message import (
    Message,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.communication import (
    COMM_DISPATCH_COMPLETE,
    COMM_DISPATCH_HANDLER_ERROR,
    COMM_DISPATCH_HANDLER_MATCHED,
    COMM_DISPATCH_NO_HANDLERS,
    COMM_DISPATCH_START,
    COMM_HANDLER_DEREGISTER_MISS,
    COMM_HANDLER_DEREGISTERED,
    COMM_HANDLER_INVALID,
    COMM_HANDLER_REGISTERED,
)

logger = get_logger(__name__)


class DispatchResult(BaseModel):
    """Immutable outcome of dispatching a single message.

    Attributes:
        message_id: The dispatched message's identifier.
        handlers_matched: Derived count (succeeded + failed).
        handlers_succeeded: Number of handlers that completed without error.
        handlers_failed: Number of handlers that raised an exception.
        errors: Error descriptions from failed handlers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    message_id: UUID
    handlers_succeeded: int = Field(ge=0)
    handlers_failed: int = Field(ge=0)
    errors: tuple[str, ...] = Field(default=())

    @computed_field
    @property
    def handlers_matched(self) -> int:
        """Total handlers that matched (succeeded + failed)."""
        return self.handlers_succeeded + self.handlers_failed


class MessageDispatcher:
    """Per-agent dispatcher that routes messages to registered handlers.

    Args:
        agent_id: Identifier of the owning agent (for logging context).
    """

    __slots__ = ("_agent_id", "_registrations")

    def __init__(self, agent_id: str = "unknown") -> None:
        self._agent_id = agent_id
        self._registrations: dict[str, HandlerRegistration] = {}

    def register(
        self,
        handler: MessageHandler | MessageHandlerFunc,
        *,
        message_types: frozenset[MessageType] | None = None,
        min_priority: MessagePriority = MessagePriority.LOW,
        name: str = "unnamed",
    ) -> str:
        """Register a handler for incoming messages.

        If *handler* is a bare async function, it is automatically wrapped
        in a :class:`FunctionHandler`.

        Args:
            handler: The handler instance or async function.
            message_types: Message types to match (empty/None = all).
            min_priority: Minimum priority to accept.
            name: Human-readable label for debugging.

        Returns:
            The unique handler registration ID.

        Raises:
            TypeError: If a ``MessageHandler`` instance has a synchronous
                ``handle()`` method.
        """
        if not isinstance(handler, MessageHandler):
            handler = FunctionHandler(handler)
        elif not inspect.iscoroutinefunction(handler.handle):
            msg = (
                f"MessageHandler {type(handler).__name__!r} has a "
                f"synchronous handle() -- must be async"
            )
            logger.warning(
                COMM_HANDLER_INVALID,
                agent_id=self._agent_id,
                handler_name=name,
                handler_type=type(handler).__name__,
                error=msg,
            )
            raise TypeError(msg)

        registration = HandlerRegistration(
            handler=handler,
            message_types=message_types or frozenset(),
            min_priority=min_priority,
            name=name,
        )
        self._registrations[registration.handler_id] = registration
        logger.info(
            COMM_HANDLER_REGISTERED,
            agent_id=self._agent_id,
            handler_id=registration.handler_id,
            handler_name=name,
        )
        return registration.handler_id

    def deregister(self, handler_id: str) -> bool:
        """Remove a previously registered handler.

        Args:
            handler_id: The registration ID returned by :meth:`register`.

        Returns:
            True if the handler was found and removed, False otherwise.
        """
        removed = self._registrations.pop(handler_id, None)
        if removed is not None:
            logger.info(
                COMM_HANDLER_DEREGISTERED,
                agent_id=self._agent_id,
                handler_id=handler_id,
                handler_name=removed.name,
            )
            return True
        logger.debug(
            COMM_HANDLER_DEREGISTER_MISS,
            agent_id=self._agent_id,
            handler_id=handler_id,
        )
        return False

    async def dispatch(self, message: Message) -> DispatchResult:
        """Route a message to all matching handlers concurrently.

        Most handler ``Exception`` subclasses are isolated; their
        errors are captured into the per-handler ``errors`` slot
        without affecting siblings. ``MemoryError`` and
        ``RecursionError`` are deliberate carve-outs: the interpreter
        is in catastrophic state, so they re-raise to abort the
        ``TaskGroup`` and cancel all remaining handlers rather than
        being absorbed silently. ``BaseException`` subclasses (e.g.
        ``KeyboardInterrupt``, ``asyncio.CancelledError``) likewise
        propagate through the ``TaskGroup``, cancelling all remaining
        handlers.

        Args:
            message: The message to dispatch.

        Returns:
            A :class:`DispatchResult` summarising the outcome.
        """
        matched = [
            reg for reg in self._registrations.values() if self._matches(reg, message)
        ]

        logger.debug(
            COMM_DISPATCH_START,
            agent_id=self._agent_id,
            message_id=str(message.id),
            message_type=message.type,
        )

        if not matched:
            logger.debug(
                COMM_DISPATCH_NO_HANDLERS,
                agent_id=self._agent_id,
                message_id=str(message.id),
            )
            return DispatchResult(
                message_id=message.id,
                handlers_succeeded=0,
                handlers_failed=0,
            )

        logger.debug(
            COMM_DISPATCH_HANDLER_MATCHED,
            agent_id=self._agent_id,
            message_id=str(message.id),
            count=len(matched),
        )

        errors: list[str | None] = [None] * len(matched)

        async with asyncio.TaskGroup() as tg:
            for idx, reg in enumerate(matched):
                _ = tg.create_task(
                    self._guarded_handle(reg, message, errors, idx),
                )

        error_msgs = tuple(e for e in errors if e is not None)
        succeeded = len(matched) - len(error_msgs)

        logger.info(
            COMM_DISPATCH_COMPLETE,
            agent_id=self._agent_id,
            message_id=str(message.id),
            matched=len(matched),
            succeeded=succeeded,
            failed=len(error_msgs),
        )

        return DispatchResult(
            message_id=message.id,
            handlers_succeeded=succeeded,
            handlers_failed=len(error_msgs),
            errors=error_msgs,
        )

    async def _guarded_handle(
        self,
        registration: HandlerRegistration,
        message: Message,
        errors: list[str | None],
        index: int,
    ) -> None:
        """Execute a single handler, capturing Exception errors.

        Args:
            registration: The handler registration to invoke.
            message: The message to pass to the handler.
            errors: Pre-allocated error list (indexed by handler).
            index: Position in the error list for this handler.

        Raises:
            MemoryError: Re-raised (never absorbed) so the wrapping
                TaskGroup cancels siblings and surfaces the failure.
            RecursionError: Re-raised for the same reason as
                ``MemoryError``.
        """
        try:
            await registration.handler.handle(message)
        except (MemoryError, RecursionError) as exc:
            # Catastrophic interpreter state: do NOT absorb these into
            # errors[index] -- they must propagate through the wrapping
            # TaskGroup so sibling handlers cancel and the process can
            # surface the failure rather than silently degrading. Log
            # via ``log_exception_redacted`` (no traceback attachment,
            # so frame-locals stay out of the event) before re-raising,
            # per the redacted-error logging discipline in CLAUDE.md
            # ``## Logging`` -- never ``logger.exception`` /
            # ``exc_info=True`` / ``span.record_exception``.
            log_exception_redacted(
                logger,
                COMM_DISPATCH_HANDLER_ERROR,
                exc,
                agent_id=self._agent_id,
                message_id=str(message.id),
                handler_id=registration.handler_id,
                handler_name=registration.name,
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            safe_error = safe_error_description(exc)
            errors[index] = safe_error
            logger.warning(
                COMM_DISPATCH_HANDLER_ERROR,
                agent_id=self._agent_id,
                message_id=str(message.id),
                handler_id=registration.handler_id,
                handler_name=registration.name,
                error_type=type(exc).__name__,
                error=safe_error,
            )

    @staticmethod
    def _matches(
        registration: HandlerRegistration,
        message: Message,
    ) -> bool:
        """Check whether a registration's filters match a message.

        Args:
            registration: The handler registration to check.
            message: The incoming message.

        Returns:
            True if the message passes all filters.
        """
        if (
            registration.message_types
            and message.type not in registration.message_types
        ):
            return False
        return priority_at_least(message.priority, registration.min_priority)
