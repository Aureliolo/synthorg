"""Event stream hub -- single pub/sub source for SSE consumers.

The ``EventStreamHub`` is the shared event source for all real-time
consumers: the AG-UI dashboard (internal) and the future A2A gateway
(external).  Each consumer subscribes to a session-scoped queue and
receives projected ``StreamEvent`` objects.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from synthorg.observability import get_logger
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_HUB_PUBLISH_FAILED,
)

logger = get_logger(__name__)

_DEFAULT_MAX_QUEUE_SIZE = 256


class EventStreamHub:
    """In-memory pub/sub hub for real-time event streaming.

    Session-scoped: each SSE client subscribes to events for a
    ``session_id``.  The hub holds per-session queues and fans out
    events to all subscribers for the matching session.

    Args:
        max_queue_size: Maximum events buffered per subscriber queue.
            When full, new events are dropped (never blocks the
            publisher).
    """

    __slots__ = ("_lock", "_max_queue_size", "_subscribers")

    def __init__(
        self,
        max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[str, list[asyncio.Queue[StreamEvent]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        session_id: str,
    ) -> asyncio.Queue[StreamEvent]:
        """Subscribe to events for a session.

        Args:
            session_id: Session to subscribe to.

        Returns:
            An asyncio queue that will receive events for this session.
        """
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(
            maxsize=self._max_queue_size,
        )
        async with self._lock:
            self._subscribers.setdefault(session_id, []).append(queue)
        return queue

    async def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[StreamEvent],
    ) -> None:
        """Remove a subscriber queue.

        Args:
            session_id: Session the queue belongs to.
            queue: The queue to remove.
        """
        async with self._lock:
            queues = self._subscribers.get(session_id)
            if queues is None:
                return
            with contextlib.suppress(ValueError):
                queues.remove(queue)
            if not queues:
                del self._subscribers[session_id]

    async def publish(self, event: StreamEvent) -> None:
        """Fan out an event to all subscribers for its session.

        Best-effort: if a subscriber queue is full, the event is
        dropped for that subscriber (never blocks the publisher).

        The subscriber list is snapshotted under the lock and
        ``put_nowait`` is invoked outside the lock so a slow consumer's
        ``QueueFull`` warning cannot serialize other publishers behind
        the dispatch.

        Args:
            event: The stream event to publish.
        """
        async with self._lock:
            queues_snapshot = list(self._subscribers.get(event.session_id, ()))
        if not queues_snapshot:
            return
        for queue in queues_snapshot:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    EVENT_STREAM_HUB_PUBLISH_FAILED,
                    session_id=event.session_id,
                    event_id=event.id,
                    note="Subscriber queue full, event dropped",
                )

    async def publish_raw(
        self,
        *,
        session_id: str,
        event_type: AgUiEventType,
        agent_id: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Build a StreamEvent and publish it.

        Convenience method that constructs a ``StreamEvent`` with an
        auto-generated ID and current timestamp.

        Args:
            session_id: Target session.
            event_type: AG-UI event type.
            agent_id: Producing agent, if applicable.
            correlation_id: Correlation identifier for tracing.
            payload: Event-specific data.
        """
        event = StreamEvent(
            id=f"evt-{uuid4().hex}",
            type=event_type,
            timestamp=datetime.now(UTC),
            session_id=session_id,
            correlation_id=correlation_id,
            agent_id=agent_id,
            payload=payload or {},
        )
        await self.publish(event)
