"""Event stream hub -- single pub/sub source for SSE consumers.

The ``EventStreamHub`` is the shared event source for all real-time
consumers: the AG-UI dashboard (internal) and the future A2A gateway
(external).  Each consumer subscribes to a session-scoped queue and
receives projected ``StreamEvent`` objects.
"""

import asyncio
import contextlib
from collections import OrderedDict
from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_HUB_PUBLISH_DEDUPED,
    EVENT_STREAM_HUB_PUBLISH_FAILED,
)

logger = get_logger(__name__)

_DEFAULT_MAX_QUEUE_SIZE = 256
_DEFAULT_DEDUP_TTL_SECONDS = 60.0
_DEFAULT_DEDUP_MAX_ENTRIES_PER_SESSION = 1024


class EventStreamHub:
    """In-memory pub/sub hub for real-time event streaming.

    Session-scoped: each SSE client subscribes to events for a
    ``session_id``.  The hub holds per-session queues and fans out
    events to all subscribers for the matching session.

    Args:
        max_queue_size: Maximum events buffered per subscriber queue.
            When full, new events are dropped (never blocks the
            publisher).
        dedup_ttl_seconds: TTL for the per-session dedup window in
            seconds. Identical ``event.id`` values published within
            this window are skipped. ``0`` disables the time-based
            eviction (entries only fall out via the per-session size
            bound). Default 60.
        dedup_max_entries_per_session: Maximum dedup entries kept per
            session. When the bound is hit, the oldest entry is
            evicted FIFO. Bounds memory growth even for noisy
            sessions that never get TTL-evicted. Default 1024.
        clock: Time source used for the dedup TTL. Inject a
            ``FakeClock`` from ``tests._shared.fake_clock`` to drive
            virtual time in tests; production wiring leaves this
            ``None`` so the hub uses ``SystemClock``.
    """

    __slots__ = (
        "_clock",
        "_dedup_max_entries_per_session",
        "_dedup_ttl_seconds",
        "_lock",
        "_max_queue_size",
        "_seen_event_ids",
        "_subscribers",
    )

    def __init__(
        self,
        max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE,
        *,
        dedup_ttl_seconds: float = _DEFAULT_DEDUP_TTL_SECONDS,
        dedup_max_entries_per_session: int = _DEFAULT_DEDUP_MAX_ENTRIES_PER_SESSION,
        clock: Clock | None = None,
    ) -> None:
        # Fail-fast on bad inputs; otherwise the trim loop in
        # ``_record_published_locked`` would call ``popitem`` on an
        # empty OrderedDict at publish time when
        # ``dedup_max_entries_per_session`` is non-positive, and a
        # negative TTL would short-circuit the eviction sweep into
        # always-stale (every entry instantly "expired").
        if max_queue_size < 1:
            msg = f"max_queue_size must be >= 1, got {max_queue_size}"
            raise ValueError(msg)
        if dedup_ttl_seconds < 0:
            msg = f"dedup_ttl_seconds must be >= 0, got {dedup_ttl_seconds}"
            raise ValueError(msg)
        if dedup_max_entries_per_session < 1:
            msg = (
                "dedup_max_entries_per_session must be >= 1, got "
                f"{dedup_max_entries_per_session}"
            )
            raise ValueError(msg)
        self._max_queue_size = max_queue_size
        self._dedup_ttl_seconds = dedup_ttl_seconds
        self._dedup_max_entries_per_session = dedup_max_entries_per_session
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._subscribers: dict[str, list[asyncio.Queue[StreamEvent]]] = {}
        # Per-session insertion-ordered map of ``event.id`` ->
        # ``monotonic_seen_at``. Bounded per session and TTL-evicted on
        # publish so a long-lived session cannot grow the dedup window
        # without bound. Without this map, retried publishes (e.g. a
        # webhook handler that catches a transient publish failure and
        # retries) would emit the same event twice to all subscribers.
        self._seen_event_ids: dict[str, OrderedDict[str, float]] = {}
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
                # Drop the per-session dedup window once the last
                # subscriber leaves so a long-lived hub with churn
                # cannot leak per-session state for sessions that
                # have no subscribers. The TTL eviction in
                # ``publish()`` only fires on publishes; without this
                # cleanup, the dedup map for a finished session would
                # only shed entries on the rare case of a stray
                # publish to that session.
                self._seen_event_ids.pop(session_id, None)

    async def publish(self, event: StreamEvent) -> None:
        """Fan out an event to all subscribers for its session.

        Best-effort: if a subscriber queue is full, the event is
        dropped for that subscriber (never blocks the publisher).

        Deduplicates by ``event.id`` within a per-session sliding
        window so an upstream retry (e.g. webhook handler that
        catches a transient publish failure and retries) cannot
        double-deliver. The first publish wins; subsequent publishes
        with the same id within the TTL are skipped and logged.

        The subscriber list is snapshotted under the lock and
        ``put_nowait`` is invoked outside the lock so a slow consumer's
        ``QueueFull`` warning cannot serialize other publishers behind
        the dispatch.

        Args:
            event: The stream event to publish.
        """
        now = self._clock.monotonic()
        async with self._lock:
            queues_snapshot = list(self._subscribers.get(event.session_id, ()))
            # If no subscribers, the event would be dropped anyway.
            # Don't record it in the dedup window: a later retry that
            # arrives after the client reconnects within the TTL must
            # be delivered, not silently suppressed because the first
            # attempt fell on an empty session. Also drop any orphan
            # dedup-window state for that session so it cannot grow
            # without bound across publish-without-subscribers cycles.
            if not queues_snapshot:
                self._seen_event_ids.pop(event.session_id, None)
                return
            if self._is_duplicate_locked(event, now):
                logger.warning(
                    EVENT_STREAM_HUB_PUBLISH_DEDUPED,
                    session_id=event.session_id,
                    event_id=event.id,
                    ttl_seconds=self._dedup_ttl_seconds,
                )
                return
            self._record_published_locked(event, now)
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

    def _is_duplicate_locked(self, event: StreamEvent, now: float) -> bool:
        """Return ``True`` if *event* was already published within the TTL.

        Caller must hold ``self._lock``. Evicts expired entries from
        the per-session window before testing membership so a stale
        entry does not falsely register as a duplicate.
        """
        seen = self._seen_event_ids.get(event.session_id)
        if seen is None:
            return False
        cutoff = now - self._dedup_ttl_seconds
        while seen:
            oldest_id, oldest_ts = next(iter(seen.items()))
            if oldest_ts >= cutoff:
                break
            del seen[oldest_id]
        if not seen:
            del self._seen_event_ids[event.session_id]
            return False
        return event.id in seen

    def _record_published_locked(
        self,
        event: StreamEvent,
        now: float,
    ) -> None:
        """Record *event* as published in the per-session dedup window.

        Caller must hold ``self._lock``. Bounds each session's window
        by ``self._dedup_max_entries_per_session`` so a single noisy
        session cannot exhaust memory.
        """
        seen = self._seen_event_ids.setdefault(event.session_id, OrderedDict())
        seen[event.id] = now
        while len(seen) > self._dedup_max_entries_per_session:
            seen.popitem(last=False)

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
