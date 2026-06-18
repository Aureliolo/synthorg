"""Event stream hub -- single pub/sub source for SSE consumers.

The ``EventStreamHub`` is the shared event source for all real-time
consumers: the AG-UI dashboard (internal) and the future A2A gateway
(external).  Each consumer subscribes to a session-scoped queue and
receives projected ``StreamEvent`` objects.

The hub owns an optional inactivity-TTL janitor task that prunes
subscribers whose queues have not received an event within a
configurable idle window.  Without the janitor, a client that
crashes or disconnects without calling ``unsubscribe`` (HTTP keep-
alive drop, network partition, browser-tab kill) would leak its
queue + dedup-window state for the lifetime of the process. The
janitor only runs while ``start()`` -> ``stop()`` lifecycle has been
invoked; tests that construct a hub without lifecycle wiring keep
the legacy synchronous behaviour.
"""

import asyncio
import contextlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Final
from uuid import uuid4

from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_HUB_JANITOR_FAILED,
    EVENT_STREAM_HUB_JANITOR_PRUNED,
    EVENT_STREAM_HUB_PUBLISH_DEDUPED,
    EVENT_STREAM_HUB_PUBLISH_FAILED,
    EVENT_STREAM_HUB_START_REJECTED,
    EVENT_STREAM_HUB_STARTED,
    EVENT_STREAM_HUB_STOP_TIMEOUT,
    EVENT_STREAM_HUB_STOPPED,
)

logger = get_logger(__name__)

_DEFAULT_MAX_QUEUE_SIZE: Final[int] = 256
_DEFAULT_DEDUP_TTL_SECONDS: Final[float] = 60.0
_DEFAULT_DEDUP_MAX_ENTRIES_PER_SESSION: Final[int] = 1024
_DEFAULT_SUBSCRIBER_IDLE_TTL_SECONDS: Final[float] = 86400.0
_DEFAULT_JANITOR_INTERVAL_SECONDS: Final[float] = 300.0
_DEFAULT_JANITOR_STOP_TIMEOUT_SECONDS: Final[float] = 10.0


class EventStreamHubUnrestartableError(ConflictError):
    """Raised when ``start()`` is called on a hub that timed out during ``stop()``.

    Per the lifecycle-sync contract, a service whose ``stop()`` drain hits its
    hard deadline cannot be safely restarted: a late ``start()`` would stack a
    new janitor on top of an orphaned task that ignored cancellation.
    Operators must construct a fresh hub instead.
    """

    default_message: ClassVar[str] = (
        "Event stream hub is unrestartable after a timed-out stop"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    status_code: ClassVar[int] = 409


# Intentionally NOT frozen: ``last_active`` is mutated in-place under
# ``EventStreamHub._lock`` per the docstring below. CLAUDE.md "Frozen
# by default" deviation is justified because allocating a fresh
# ``_Subscriber`` on every successful publish would churn the hot
# fan-out path.
@dataclass(slots=True)
class _Subscriber:
    """Per-subscriber bookkeeping owned by ``EventStreamHub``.

    ``last_active`` carries the monotonic timestamp of the most recent
    activity (subscribe call or successful publish to this subscriber).
    The janitor reads ``last_active`` to evict idle subscribers; the
    field is mutated in-place under ``EventStreamHub._lock`` so all
    reads / writes happen-before each other through the lock.
    """

    queue: asyncio.Queue[StreamEvent] = field()
    last_active: float = field()


class EventStreamSubscription:
    """Opaque handle to a session's event stream.

    Returned by :meth:`EventStreamHub.subscribe`. Callers consume events
    via :meth:`get` and release the subscription through
    :meth:`EventStreamHub.unsubscribe`, so the hub's internal
    ``asyncio.Queue`` never crosses the boundary.
    """

    __slots__ = ("_queue", "session_id")

    def __init__(
        self,
        session_id: str,
        queue: asyncio.Queue[StreamEvent],
    ) -> None:
        self.session_id = session_id
        self._queue = queue

    async def get(self) -> StreamEvent:
        """Await the next event delivered to this subscription.

        Returns:
            The next :class:`StreamEvent` for the subscription's session.
        """
        return await self._queue.get()

    def get_nowait(self) -> StreamEvent:
        """Return the next buffered event without blocking.

        Returns:
            The next :class:`StreamEvent`.

        Raises:
            asyncio.QueueEmpty: When no event is currently buffered.
        """
        return self._queue.get_nowait()

    def empty(self) -> bool:
        """Return whether the subscription has no buffered events.

        Returns:
            ``True`` when no event is currently buffered.
        """
        return self._queue.empty()

    def qsize(self) -> int:
        """Return the number of currently buffered events.

        Returns:
            The buffered-event count.
        """
        return self._queue.qsize()


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
        clock: Time source used for the dedup TTL and the janitor's
            inactivity check. Inject a ``FakeClock`` from
            ``tests._shared.fake_clock`` to drive virtual time in
            tests; production wiring leaves this ``None`` so the hub
            uses ``SystemClock``.
    """

    __slots__ = (
        "_clock",
        "_dedup_max_entries_per_session",
        "_dedup_ttl_seconds",
        "_janitor_task",
        "_lifecycle_lock",
        "_lifecycle_lock_loop",
        "_lock",
        "_lock_loop",
        "_max_queue_size",
        "_running",
        "_seen_event_ids",
        "_stop_failed",
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
        self._subscribers: dict[str, list[_Subscriber]] = {}
        # Per-session insertion-ordered map of ``event.id`` ->
        # ``monotonic_seen_at``. Bounded per session and TTL-evicted on
        # publish so a long-lived session cannot grow the dedup window
        # without bound. Without this map, retried publishes (e.g. a
        # webhook handler that catches a transient publish failure and
        # retries) would emit the same event twice to all subscribers.
        self._seen_event_ids: dict[str, OrderedDict[str, float]] = {}
        # Loop-bound asyncio primitives are deferred until first use
        # so the hub can be safely re-used across event loops (test
        # scenarios where pytest-asyncio creates a fresh loop per
        # test while a session-scoped Litestar app holds this hub).
        # ``_lock`` is operational (every subscribe/publish acquires
        # it) and ``_lifecycle_lock`` is lifecycle-only.  Both rebind
        # to the running loop on first access via the
        # ``_*_for_current_loop`` helpers.
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._lifecycle_lock_loop: asyncio.AbstractEventLoop | None = None
        self._janitor_task: asyncio.Task[None] | None = None
        self._running = False
        self._stop_failed = False

    def __del__(self) -> None:
        """Cancel an orphaned janitor task if the hub is GC'd un-stopped.

        The supported teardown path is ``stop()``; this is a defensive
        safety net so a hub dropped without it (a caller that forgot, or
        a failed wiring path) does not leave the janitor running forever
        holding a reference to the hub. Best-effort and exception-safe:
        ``cancel()`` is a no-op on a done task and may raise if the loop
        is already closed, so the failure is suppressed.
        """
        task = getattr(self, "_janitor_task", None)
        if task is not None and not task.done():
            with contextlib.suppress(RuntimeError):
                task.cancel()

    def _lock_for_current_loop(self) -> asyncio.Lock:
        """Operational lock bound to the running loop, rebinding if needed.

        ``_lock`` is acquired by every subscribe/publish call.  Lazy
        creation + per-loop rebind keeps the hub usable across
        pytest-asyncio's per-test loops without false-sharing locks
        between unrelated tests.

        Returns:
            The operational lock bound to the current event loop.
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            if self._lock is None:
                self._lock = asyncio.Lock()
            return self._lock
        if self._lock is None or self._lock_loop is not current:
            self._lock = asyncio.Lock()
            self._lock_loop = current
        return self._lock

    def _lifecycle_lock_for_current_loop(self) -> asyncio.Lock:
        """Lifecycle lock bound to the running loop, rebinding if needed.

        Returns:
            The lifecycle lock bound to the current event loop.
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            if self._lifecycle_lock is None:
                self._lifecycle_lock = asyncio.Lock()
            return self._lifecycle_lock
        if self._lifecycle_lock is None or self._lifecycle_lock_loop is not current:
            self._lifecycle_lock = asyncio.Lock()
            self._lifecycle_lock_loop = current
        return self._lifecycle_lock

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(
        self,
        *,
        idle_ttl_seconds: float = _DEFAULT_SUBSCRIBER_IDLE_TTL_SECONDS,
        janitor_interval_seconds: float = _DEFAULT_JANITOR_INTERVAL_SECONDS,
    ) -> None:
        """Spawn the inactivity-TTL janitor task.

        Idempotent: a second ``start()`` while the janitor is already
        running is a no-op. The hub continues to function without the
        janitor when ``start()`` is never invoked (the legacy path).

        Args:
            idle_ttl_seconds: Subscribers whose ``last_active`` is older
                than this many seconds are pruned by the janitor.
            janitor_interval_seconds: Wall-clock interval between janitor
                sweeps. The janitor sleeps via the injected ``Clock`` so
                ``FakeClock`` advances cleanly in tests.

        Raises:
            EventStreamHubUnrestartableError: If a previous ``stop()``
                hit its hard deadline. Construct a fresh hub instead.
            ValueError: If either argument is non-positive.
        """
        if idle_ttl_seconds <= 0:
            msg = f"idle_ttl_seconds must be > 0, got {idle_ttl_seconds}"
            raise ValueError(msg)
        if janitor_interval_seconds <= 0:
            msg = (
                f"janitor_interval_seconds must be > 0, got {janitor_interval_seconds}"
            )
            raise ValueError(msg)
        async with self._lifecycle_lock_for_current_loop():
            # If the hub instance survived a previous loop teardown
            # (shared-app conftest path: hub lives across TestClients,
            # each of which gets a fresh loop), the recorded janitor
            # task is bound to a dead loop. Treat it as gone so this
            # ``start()`` spawns a fresh janitor on the live loop.
            current_loop = asyncio.get_running_loop()
            task = self._janitor_task
            if task is not None and (
                task.done() or task.get_loop() is not current_loop
            ):
                self._janitor_task = None
                self._running = False

            if self._stop_failed:
                msg = (
                    "EventStreamHub cannot restart after a timed-out"
                    " stop(); construct a fresh instance"
                )
                logger.warning(
                    EVENT_STREAM_HUB_START_REJECTED,
                    reason="unrestartable",
                    error_type=EventStreamHubUnrestartableError.__name__,
                )
                raise EventStreamHubUnrestartableError(msg)
            if self._running:
                return
            self._running = True
            self._janitor_task = asyncio.create_task(
                self._janitor_loop(
                    idle_ttl_seconds=idle_ttl_seconds,
                    janitor_interval_seconds=janitor_interval_seconds,
                ),
                name="event-stream-hub-janitor",
            )
            logger.info(
                EVENT_STREAM_HUB_STARTED,
                idle_ttl_seconds=idle_ttl_seconds,
                janitor_interval_seconds=janitor_interval_seconds,
            )

    async def stop(
        self,
        *,
        stop_timeout_seconds: float = _DEFAULT_JANITOR_STOP_TIMEOUT_SECONDS,
    ) -> None:
        """Cancel the janitor task and drain it within ``stop_timeout_seconds``.

        If the drain exceeds the deadline, the hub is marked
        unrestartable and the orphaned task is left behind for the
        process to outlive. Subscribers are NOT cleared -- callers can
        still drain queues after ``stop()``.

        Idempotent: ``stop()`` on a hub that is not running is a no-op.
        """
        async with self._lifecycle_lock_for_current_loop():
            if not self._running:
                return
            self._running = False
            task = self._janitor_task
            self._janitor_task = None
            if task is None:
                logger.info(EVENT_STREAM_HUB_STOPPED)
                return
            current_loop = asyncio.get_running_loop()
            if task.get_loop() is not current_loop:
                # The recorded janitor was spawned on a now-dead loop;
                # we cannot cancel or await it from here. Drop it.
                logger.info(EVENT_STREAM_HUB_STOPPED)
                return
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=stop_timeout_seconds)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                self._stop_failed = True
                logger.error(
                    EVENT_STREAM_HUB_STOP_TIMEOUT,
                    stop_timeout_seconds=stop_timeout_seconds,
                )
                return
            logger.info(EVENT_STREAM_HUB_STOPPED)

    async def _janitor_loop(
        self,
        *,
        idle_ttl_seconds: float,
        janitor_interval_seconds: float,
    ) -> None:
        """Periodically prune subscribers idle past ``idle_ttl_seconds``.

        A prune failure (lock acquisition error, clock failure, dict-
        mutation race) must not kill the loop -- otherwise the hub
        silently stops reclaiming memory and the original leak the
        janitor was added to fix returns. Re-raise only the system-
        level errors (``CancelledError``, ``MemoryError``,
        ``RecursionError``); log every other exception and continue.

        Raises:
            asyncio.CancelledError: Propagated on shutdown so the janitor
                task stops cleanly.
        """
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
        while True:
            await self._clock.sleep(janitor_interval_seconds)
            try:
                await self._prune_idle_subscribers(idle_ttl_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    EVENT_STREAM_HUB_JANITOR_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    async def _prune_idle_subscribers(self, idle_ttl_seconds: float) -> None:
        """Drop subscribers whose ``last_active`` is older than the TTL.

        Public-ish: also exercised directly from tests so the prune
        invariant can be asserted without driving the janitor task.
        """
        now = self._clock.monotonic()
        cutoff = now - idle_ttl_seconds
        pruned = 0
        async with self._lock_for_current_loop():
            for session_id in list(self._subscribers):
                kept = [
                    sub
                    for sub in self._subscribers[session_id]
                    if sub.last_active >= cutoff
                ]
                pruned += len(self._subscribers[session_id]) - len(kept)
                if kept:
                    self._subscribers[session_id] = kept
                else:
                    del self._subscribers[session_id]
                    self._seen_event_ids.pop(session_id, None)
        if pruned > 0:
            logger.info(
                EVENT_STREAM_HUB_JANITOR_PRUNED,
                pruned_subscribers=pruned,
                remaining_sessions=len(self._subscribers),
                idle_ttl_seconds=idle_ttl_seconds,
            )

    # ── Subscribe / unsubscribe / publish ────────────────────────

    async def subscribe(
        self,
        session_id: str,
    ) -> EventStreamSubscription:
        """Subscribe to events for a session.

        Args:
            session_id: Session to subscribe to.

        Returns:
            An :class:`EventStreamSubscription` handle that delivers
            events for this session. The backing queue stays internal to
            the hub.
        """
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(
            maxsize=self._max_queue_size,
        )
        subscriber = _Subscriber(queue=queue, last_active=self._clock.monotonic())
        async with self._lock_for_current_loop():
            self._subscribers.setdefault(session_id, []).append(subscriber)
        return EventStreamSubscription(session_id, queue)

    async def unsubscribe(
        self,
        subscription: EventStreamSubscription,
    ) -> None:
        """Remove a subscription.

        Args:
            subscription: The handle returned by :meth:`subscribe`.
        """
        session_id = subscription.session_id
        queue = subscription._queue  # noqa: SLF001 -- same-module internal access
        async with self._lock_for_current_loop():
            subs = self._subscribers.get(session_id)
            if subs is None:
                return
            self._subscribers[session_id] = [
                sub for sub in subs if sub.queue is not queue
            ]
            if not self._subscribers[session_id]:
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

        Successful per-subscriber deliveries also bump ``last_active``
        so a session that is actively producing events never gets
        evicted by the inactivity-TTL janitor.

        The subscriber list is snapshotted under the lock and
        ``put_nowait`` is invoked outside the lock so a slow consumer's
        ``QueueFull`` warning cannot serialize other publishers behind
        the dispatch.

        Args:
            event: The stream event to publish.
        """
        now = self._clock.monotonic()
        async with self._lock_for_current_loop():
            subs_snapshot = list(self._subscribers.get(event.session_id, ()))
            # If no subscribers, the event would be dropped anyway.
            # Don't record it in the dedup window: a later retry that
            # arrives after the client reconnects within the TTL must
            # be delivered, not silently suppressed because the first
            # attempt fell on an empty session. Also drop any orphan
            # dedup-window state for that session so it cannot grow
            # without bound across publish-without-subscribers cycles.
            if not subs_snapshot:
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
            # Mutate ``last_active`` while the lock is still held so the
            # janitor's read at ``_prune_idle_subscribers`` sees a value
            # that happens-before the publish through ``_lock``. Doing
            # the put_nowait inside the lock would gate concurrent
            # publishers behind a slow consumer, so the queue write
            # itself stays out -- but the timestamp bump is cheap and
            # belongs under the same invariant the dataclass docstring
            # promises. ``put_nowait`` happens after the lock is
            # released; if the queue is full the timestamp is still
            # bumped to "intent to publish" rather than "delivery
            # confirmed", which is the right signal for the janitor.
            for sub in subs_snapshot:
                sub.last_active = now
        for sub in subs_snapshot:
            try:
                sub.queue.put_nowait(event)
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
        # ``dedup_ttl_seconds == 0`` is the documented "disable
        # time-based eviction" knob: entries fall out only via the
        # per-session size bound. Without this guard the eviction
        # cutoff would equal ``now`` and every previously recorded
        # entry would test as expired (its ``oldest_ts`` was captured
        # at an earlier monotonic reading), draining the window on
        # every publish and silently disabling deduplication too.
        if self._dedup_ttl_seconds > 0:
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
