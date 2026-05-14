"""In-process registry of awaited escalation Futures.

When :class:`HumanEscalationResolver` awaits a decision, it registers
an ``asyncio.Future`` under the escalation ID.  The decision REST
endpoint resolves the Future so the awaiting coroutine wakes with the
operator's payload.

The registry is process-local by design: coroutines awaiting a Future
live in the same event loop that will resolve them.  Decisions arriving
on a different process (e.g. a future Kubernetes deployment) have to
go through the persistent store, which is the durable source of truth.
"""

import asyncio

from synthorg.communication.conflict_resolution.escalation.models import (
    EscalationDecision,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_CANCELLED,
    CONFLICT_ESCALATION_QUEUED,
    CONFLICT_ESCALATION_RESOLVED,
)

logger = get_logger(__name__)


class PendingFuturesRegistry:
    """Process-local map of escalation ID -> ``asyncio.Future``."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._futures: dict[str, asyncio.Future[EscalationDecision]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        escalation_id: str,
    ) -> asyncio.Future[EscalationDecision]:
        """Return a fresh Future for ``escalation_id``.

        Raises:
            ValueError: A Future is already registered for this ID.
        """
        async with self._lock:
            if escalation_id in self._futures:
                msg = f"Future already registered for escalation {escalation_id!r}"
                logger.warning(
                    CONFLICT_ESCALATION_QUEUED,
                    escalation_id=escalation_id,
                    note="duplicate_future_register",
                )
                raise ValueError(msg)
            loop = asyncio.get_running_loop()
            future: asyncio.Future[EscalationDecision] = loop.create_future()
            self._futures[escalation_id] = future
        # Log + return AFTER releasing the lock so the structured-log
        # I/O does not extend the critical section. The Future is
        # already published into ``self._futures`` under the lock, so a
        # ``resolve()`` racing this return point sees the entry and
        # completes the Future correctly -- the lock orders the map
        # mutation, not the bookkeeping log line.
        logger.info(
            CONFLICT_ESCALATION_QUEUED,
            escalation_id=escalation_id,
            note="future_registered",
        )
        return future

    async def resolve(
        self,
        escalation_id: str,
        decision: EscalationDecision,
    ) -> bool:
        """Wake the Future associated with ``escalation_id``.

        Returns:
            ``True`` if a Future was resolved.  ``False`` when no
            Future is registered -- decisions arriving after a restart
            (or on a different worker, before the cross-instance
            subscriber forwards them) have no awaiting coroutine; the
            persistent row is still updated by the caller.
        """
        async with self._lock:
            future = self._futures.pop(escalation_id, None)
            already_done = future is not None and future.done()
            if future is not None and not already_done:
                future.set_result(decision)
        if future is None:
            # Expected when a decision arrives after a restart or on a
            # worker that never registered the Future (the row is still
            # updated by the caller, so this is not a lost signal).
            logger.debug(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                note="no_future_registered",
            )
            return False
        if already_done:
            logger.debug(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                note="future_already_done",
            )
        else:
            logger.info(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                note="future_resolved",
            )
        return True

    async def cancel(self, escalation_id: str) -> bool:
        """Cancel the Future associated with ``escalation_id``.

        Returns:
            ``True`` if a Future was cancelled, ``False`` when nothing
            was registered.
        """
        async with self._lock:
            future = self._futures.pop(escalation_id, None)
            if future is not None and not future.done():
                future.cancel()
        if future is not None:
            logger.info(
                CONFLICT_ESCALATION_CANCELLED,
                escalation_id=escalation_id,
                note="future_cancelled",
            )
        return future is not None

    async def is_registered(self, escalation_id: str) -> bool:
        """Return ``True`` if a Future exists for ``escalation_id``."""
        async with self._lock:
            return escalation_id in self._futures

    async def close(self) -> None:
        """Cancel every outstanding Future."""
        async with self._lock:
            for future in self._futures.values():
                if not future.done():
                    future.cancel()
            self._futures.clear()
