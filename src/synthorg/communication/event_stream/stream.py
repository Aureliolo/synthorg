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
from datetime import UTC, datetime
from typing import ClassVar, Final
from uuid import uuid4

from synthorg.communication.event_stream._janitor import (
    _Subscriber,
    janitor_loop,
    prune_idle_subscribers,
    resolve_janitor_float,
)
from synthorg.communication.event_stream._publish_ledger import PublishLedger
from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import ConflictError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_HUB_PUBLISH_DEDUPED,
    EVENT_STREAM_HUB_PUBLISH_FAILED,
    EVENT_STREAM_HUB_START_REJECTED,
    EVENT_STREAM_HUB_STARTED,
    EVENT_STREAM_HUB_STOP_TIMEOUT,
    EVENT_STREAM_HUB_STOPPED,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_DEFAULT_MAX_QUEUE_SIZE: Final[int] = 256
_DEFAULT_DEDUP_TTL_SECONDS: Final[float] = 60.0
_DEFAULT_DEDUP_MAX_ENTRIES_PER_SESSION: Final[int] = 1024
_DEFAULT_SUBSCRIBER_IDLE_TTL_SECONDS: Final[float] = 86400.0
_DEFAULT_JANITOR_INTERVAL_SECONDS: Final[float] = 300.0
_DEFAULT_JANITOR_STOP_TIMEOUT_SECONDS: Final[float] = 10.0
# Bounded per-session SSE replay history. Recent events are retained so a
# reconnecting client that sends ``Last-Event-ID`` can be replayed the gap
# it missed while disconnected. Capped per session (ring buffer) and across
# sessions (FIFO) so the buffer cannot grow without bound.
_DEFAULT_HISTORY_PER_SESSION: Final[int] = 256
_DEFAULT_HISTORY_MAX_SESSIONS: Final[int] = 1024


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
        "_config_resolver",
        "_janitor_task",
        "_ledger",
        "_lifecycle_lock",
        "_lifecycle_lock_loop",
        "_lock",
        "_lock_loop",
        "_max_queue_size",
        "_running",
        "_stop_failed",
        "_subscribers",
    )

    def __init__(  # noqa: PLR0913 -- tunable bounds for queue / dedup / history
        self,
        max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE,
        *,
        dedup_ttl_seconds: float = _DEFAULT_DEDUP_TTL_SECONDS,
        dedup_max_entries_per_session: int = _DEFAULT_DEDUP_MAX_ENTRIES_PER_SESSION,
        history_per_session: int = _DEFAULT_HISTORY_PER_SESSION,
        history_max_sessions: int = _DEFAULT_HISTORY_MAX_SESSIONS,
        clock: Clock | None = None,
    ) -> None:
        # ``PublishLedger`` validates the dedup / history bounds (a
        # non-positive entry cap or negative TTL would break its trim /
        # eviction loops at publish time).
        if max_queue_size < 1:
            msg = f"max_queue_size must be >= 1, got {max_queue_size}"
            raise ValueError(msg)
        self._max_queue_size = max_queue_size
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._subscribers: dict[str, list[_Subscriber]] = {}
        # Per-session replay ring buffer + publish-dedup window, both
        # consulted under ``self._lock``. See ``_publish_ledger``.
        self._ledger = PublishLedger(
            history_max_sessions=history_max_sessions,
            history_per_session=history_per_session,
            dedup_ttl_seconds=dedup_ttl_seconds,
            dedup_max_entries_per_session=dedup_max_entries_per_session,
        )
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
        # Optional live resolver: when wired (post-startup), the janitor
        # re-reads its interval + idle TTL each sweep so an operator change
        # applies without a restart. ``None`` keeps the start()-provided
        # fallbacks (tests / pre-startup rigs).
        self._config_resolver: ConfigResolverProtocol | None = None

    def set_config_resolver(self, resolver: ConfigResolverProtocol) -> None:
        """Inject the live config resolver after construction.

        Wired by ``config_apply._wire_resolver_dependents`` once the
        settings service exists so the janitor's interval + idle TTL become
        hot. Repeated calls replace the resolver (latest wins).
        """
        self._config_resolver = resolver

    def set_history_max_sessions(self, value: int) -> None:
        """Update the SSE replay LRU session cap (delegates to the ledger)."""
        self._ledger.set_history_max_sessions(value)

    def set_history_per_session(self, value: int) -> None:
        """Update the SSE replay per-session depth (delegates to the ledger)."""
        self._ledger.set_history_per_session(value)

    async def _run_prune(self, idle_ttl_fallback: float) -> None:
        """Run one idle-subscriber sweep, re-resolving the TTL first.

        The idle TTL is re-read each sweep (fail-safe to *idle_ttl_fallback*,
        the value passed to ``start()``) so an operator change applies on the
        next sweep without a restart.
        """
        idle_ttl_seconds = await resolve_janitor_float(
            self._config_resolver,
            "event_stream_subscriber_idle_ttl_seconds",
            idle_ttl_fallback,
        )
        await prune_idle_subscribers(
            clock=self._clock,
            idle_ttl_seconds=idle_ttl_seconds,
            subscribers=self._subscribers,
            forget_session=self._ledger.forget_session,
            lock=self._lock_for_current_loop(),
        )

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
                janitor_loop(
                    clock=self._clock,
                    resolve_interval=lambda: resolve_janitor_float(
                        self._config_resolver,
                        "event_stream_janitor_interval_seconds",
                        janitor_interval_seconds,
                    ),
                    prune=lambda: self._run_prune(idle_ttl_seconds),
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

    # ── Subscribe / unsubscribe / publish ────────────────────────

    async def subscribe(
        self,
        session_id: str,
        *,
        after_id: str | None = None,
    ) -> EventStreamSubscription:
        """Subscribe to events for a session.

        Args:
            session_id: Session to subscribe to.
            after_id: When set (an SSE ``Last-Event-ID``), the retained
                history events published strictly after that id are
                replayed into the new subscriber's queue before any live
                event is forwarded, so a reconnecting client recovers the
                gap it missed. An unknown / evicted id replays the whole
                retained buffer. The replay runs under the publish lock so
                a concurrent publish cannot interleave ahead of the gap.

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
            if after_id is not None:
                self._ledger.replay_history(session_id, after_id, queue)
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
                self._ledger.forget_session(session_id)

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
            # Record into the replay buffer before the no-subscriber
            # early-return so events published during a client's
            # reconnect gap are still replayable on resubscribe.
            self._ledger.record_history(event)
            subs_snapshot = list(self._subscribers.get(event.session_id, ()))
            # If no subscribers, the event would be dropped anyway.
            # Don't record it in the dedup window: a later retry that
            # arrives after the client reconnects within the TTL must
            # be delivered, not silently suppressed because the first
            # attempt fell on an empty session. Also drop any orphan
            # dedup-window state for that session so it cannot grow
            # without bound across publish-without-subscribers cycles.
            if not subs_snapshot:
                self._ledger.forget_session(event.session_id)
                return
            if self._ledger.is_duplicate(event, now):
                logger.warning(
                    EVENT_STREAM_HUB_PUBLISH_DEDUPED,
                    session_id=event.session_id,
                    event_id=event.id,
                    ttl_seconds=self._ledger.dedup_ttl_seconds,
                )
                return
            self._ledger.record_published(event, now)
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
