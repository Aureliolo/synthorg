"""Bus-coordinated rate limiter shared state.

Enables multi-worker rate limiting by publishing acquire events
to a coordination channel.  Each worker maintains a local sliding
window view of global acquires.

On ``MemoryBus`` (single-worker), the coordination channel is
in-process so the window degrades to local-only -- same code path,
no distributed benefit, minimal overhead.
"""

import asyncio
import threading
from collections import deque
from collections.abc import Callable
from types import MappingProxyType
from typing import Final
from uuid import uuid4

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType, MessageType
from synthorg.communication.message import DataPart, Message
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.integrations.errors import (
    ConnectionRateLimitError,
    IntegrationLifecycleConflictError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.integrations import (
    RATE_LIMIT_ACQUIRE_PUBLISHED,
    RATE_LIMIT_COORDINATOR_STARTED,
    RATE_LIMIT_COORDINATOR_STOPPED,
)

logger = get_logger(__name__)

_DEFAULT_MAX_RPM: Final[int] = 60
_RPM_WINDOW_SECONDS: Final[float] = 60.0

_RATELIMIT_CHANNEL = Channel(name="#ratelimit", type=ChannelType.TOPIC)
_POLL_TIMEOUT: Final[float] = 0.5
_SUBSCRIBER_PREFIX = "__ratelimit_"


class SharedRateLimitCoordinator:
    """Bus-coordinated sliding-window rate limiter.

    Args:
        bus: The message bus instance.
        connection_name: Connection to rate-limit.
        max_rpm: Maximum requests per minute (global across workers).
        clock: Clock seam for the sliding-window timestamps; tests
            inject a ``FakeClock``. Defaults to ``SystemClock``. Wall
            clock (``now().timestamp()``) is used in preference to a
            monotonic clock because window entries are published over
            the message bus to other workers, where process-local
            monotonic values would be meaningless.
    """

    def __init__(
        self,
        bus: MessageBus,
        connection_name: str,
        *,
        max_rpm: int = _DEFAULT_MAX_RPM,
        clock: Clock | None = None,
    ) -> None:
        self._bus = bus
        self._connection_name = connection_name
        self._max_rpm = max_rpm
        self._clock = clock or SystemClock()
        self._window: deque[float] = deque()
        # Eager init: ``acquire`` is a hot-path call that runs before
        # the polling task starts; the window lock must be present.
        self._window_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see above.
        self._task: asyncio.Task[None] | None = None
        # Subscriber ID includes a per-instance UUID so multiple
        # coordinators (whether in separate worker processes or in
        # the same test process) can coexist on the same bus
        # without colliding on a shared subscription slot.
        self._subscriber_id = f"{_SUBSCRIBER_PREFIX}{connection_name}_{uuid4().hex[:8]}"
        self._started = False
        self._distributed = True
        # Tracks whether ``_bus.subscribe`` actually succeeded. A
        # local-only fallback start (subscribe failed) leaves this
        # False so ``stop()`` does not call ``unsubscribe`` on a
        # subscriber the bus never registered.
        self._subscribed = False
        # Eager init: stop() must be safe before any start() call.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Survives a timed-out stop so a later start() cannot stack a
        # second poll loop on the orphaned one (canonical lifecycle
        # pattern, see docs/reference/lifecycle-sync.md).
        self._stop_failed = False
        self._stop_drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS

    async def start(self) -> None:
        """Subscribe and start the polling task."""
        async with self._lifecycle_lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        """Subscribe and start polling. Caller already holds ``_lifecycle_lock``.

        Idempotent: returns immediately when already started, so both
        ``start()`` and the ``acquire()`` fast-path can drive it through
        the lock without double-subscribing.

        Raises:
            IntegrationLifecycleConflictError: If the coordinator was
                previously stopped with a timeout and is unrestartable.
        """
        if self._stop_failed:
            msg = (
                "SharedRateLimitCoordinator is unrestartable after a "
                "timed-out stop; construct a fresh coordinator instead"
            )
            logger.warning(
                RATE_LIMIT_COORDINATOR_STARTED,
                connection_name=self._connection_name,
                error=msg,
                note="unrestartable",
            )
            raise IntegrationLifecycleConflictError(msg)
        if self._started:
            return
        try:
            await self._bus.subscribe(
                _RATELIMIT_CHANNEL.name,
                self._subscriber_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RATE_LIMIT_COORDINATOR_STARTED,
                connection_name=self._connection_name,
                error="subscribe failed, falling back to in-process",
            )
            self._distributed = False
            self._started = True
            return
        self._subscribed = True
        self._task = asyncio.create_task(
            self._poll_loop(),
            name=f"ratelimit-{self._connection_name}",
        )
        self._distributed = True
        self._started = True
        logger.debug(
            RATE_LIMIT_COORDINATOR_STARTED,
            connection_name=self._connection_name,
            max_rpm=self._max_rpm,
        )

    async def _ensure_started(self) -> None:
        """Start the coordinator exactly once, re-checking under the lock.

        The lock-free ``_started`` read keeps the hot ``acquire()`` path
        cheap once started; the slow path re-checks ``_started`` INSIDE
        ``_lifecycle_lock`` so two concurrent first-time callers cannot
        both run the subscribe/spawn sequence (the previous lock-free
        ``if not self._started: await self.start()`` left a TOCTOU gap
        between the check and the call).
        """
        if self._started:
            return
        async with self._lifecycle_lock:
            await self._start_locked()

    async def stop(self) -> None:
        """Cancel polling and unsubscribe.

        Skips ``unsubscribe`` entirely when the coordinator fell back
        to local-only mode (subscribe never succeeded): the bus has no
        registration for this subscriber id, so unsubscribing it would
        raise ``NotSubscribedError`` and leave the coordinator stuck in
        a partial-stop state on every shutdown / factory swap.

        On a genuine unsubscribe failure (a subscription that did
        exist), the started/distributed flags are left untouched and
        the exception propagates. Flipping them to ``False`` on a
        failed unsubscribe would let a later ``start()`` reuse the same
        subscriber id against a live ghost subscription and corrupt the
        coordination window.

        Raises:
            TimeoutError: If the poll-task drain exceeds
                ``_stop_drain_timeout_seconds``; the coordinator is then
                marked unrestartable.
        """
        async with self._lifecycle_lock:
            task = self._task
            if task is not None:
                task.cancel()

                async def _drain() -> None:
                    """Await the cancelled poll task, swallowing its cancellation."""
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(exc)
                        logger.warning(
                            RATE_LIMIT_COORDINATOR_STOPPED,
                            connection_name=self._connection_name,
                            error_type=type(exc).__name__,
                            error=safe_error_description(exc),
                            note="shutdown",
                        )

                drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
                try:
                    await asyncio.wait_for(
                        asyncio.shield(drain_task),
                        timeout=self._stop_drain_timeout_seconds,
                    )
                except TimeoutError:
                    self._stop_failed = True
                    logger.error(
                        RATE_LIMIT_COORDINATOR_STOPPED,
                        connection_name=self._connection_name,
                        error=(
                            "stop exceeded hard deadline; coordinator "
                            "marked unrestartable"
                        ),
                        timeout_seconds=self._stop_drain_timeout_seconds,
                    )
                    # The shielded drain keeps running orphaned past the
                    # deadline; log its eventual outcome rather than letting a
                    # later failure surface as "task exception never retrieved".
                    drain_task.add_done_callback(
                        log_task_exceptions(
                            logger,
                            RATE_LIMIT_COORDINATOR_STOPPED,
                            connection_name=self._connection_name,
                            note="orphaned_drain_after_timeout",
                        )
                    )
                    raise
                except BaseException as exc:
                    # If ``stop()`` itself is cancelled (hard process
                    # shutdown cancels us), the shielded ``drain_task`` would
                    # be left running on a closing loop. Cancel and reap it
                    # before propagating (mirrors ``backup/scheduler.py``).
                    logger.warning(
                        RATE_LIMIT_COORDINATOR_STOPPED,
                        connection_name=self._connection_name,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown_interrupted",
                    )
                    drain_task.cancel()
                    try:
                        await drain_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as drain_exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(drain_exc)
                        logger.warning(
                            RATE_LIMIT_COORDINATOR_STOPPED,
                            connection_name=self._connection_name,
                            error_type=type(drain_exc).__name__,
                            error=safe_error_description(drain_exc),
                            note="drain_task_error_on_interrupt",
                        )
                    raise
                self._task = None
            if self._subscribed:
                try:
                    await self._bus.unsubscribe(
                        _RATELIMIT_CHANNEL.name,
                        self._subscriber_id,
                    )
                except Exception as exc:
                    reraise_critical(exc)
                    logger.warning(
                        RATE_LIMIT_COORDINATOR_STOPPED,
                        connection_name=self._connection_name,
                        subscriber_id=self._subscriber_id,
                        error=(
                            "unsubscribe failed -- coordinator remains "
                            "in partial-stop state; resolve the bus "
                            "issue before calling stop() again"
                        ),
                    )
                    raise
                self._subscribed = False
            self._started = False
            self._distributed = False
            logger.debug(
                RATE_LIMIT_COORDINATOR_STOPPED,
                connection_name=self._connection_name,
            )

    async def acquire(self) -> None:
        """Acquire a rate limit slot, publish, and check the window.

        Raises:
            ConnectionRateLimitError: If the sliding window is full.
        """
        await self._ensure_started()

        async with self._window_lock:
            now = self._clock.now().timestamp()
            self._evict_old(now)

            if len(self._window) >= self._max_rpm:
                msg = (
                    f"Rate limit exceeded for connection "
                    f"'{self._connection_name}' ({self._max_rpm} rpm)"
                )
                raise ConnectionRateLimitError(msg)

            self._window.append(now)
            # Snapshot ``_distributed`` under ``_window_lock`` so the
            # publish decision below cannot race a concurrent
            # ``_poll_loop`` flipping the flag between the check and the
            # publish.
            distributed = self._distributed
        # Skip the publish path entirely once we have fallen back to
        # local-only mode. Retrying a broken bus publish on every
        # acquire would otherwise flood the logs with warnings and
        # never actually resubscribe (the coordinator has no retry
        # policy -- the fall-back is terminal until ``stop()`` +
        # ``start()`` recreates the subscription).
        if distributed:
            await self._publish_acquire(now)

    def _evict_old(self, now: float) -> None:
        """Remove every entry older than the RPM sliding window.

        Filters the whole deque rather than only its prefix: ``_ingest``
        appends remote acquire timestamps in bus-delivery order, not
        chronological order, so an expired entry can sit behind a newer
        one. A prefix-only ``popleft`` sweep would leave such stragglers
        in place, inflating the window count and causing false denials.
        """
        cutoff = now - _RPM_WINDOW_SECONDS
        self._window = deque(ts for ts in self._window if ts >= cutoff)

    async def _publish_acquire(self, acquired_at: float) -> None:
        """Publish an acquire event for other workers."""
        if not self._distributed:
            return
        message = Message(
            timestamp=self._clock.now(),
            sender=f"ratelimit:{self._connection_name}",
            to=_RATELIMIT_CHANNEL.name,
            type=MessageType.ANNOUNCEMENT,
            channel=_RATELIMIT_CHANNEL.name,
            parts=(
                DataPart(
                    data=MappingProxyType(
                        {
                            "connection_name": self._connection_name,
                            "timestamp": acquired_at,
                        }
                    ),
                ),
            ),
        )
        try:
            await self._bus.publish(message)
            logger.debug(
                RATE_LIMIT_ACQUIRE_PUBLISHED,
                connection_name=self._connection_name,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._distributed = False
            logger.warning(
                RATE_LIMIT_ACQUIRE_PUBLISHED,
                connection_name=self._connection_name,
                error="bus publish failed, falling back to local-only",
            )

    async def _poll_loop(self) -> None:
        """Poll the coordination channel for acquire events."""
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
        while True:
            try:
                envelope = await self._bus.receive(
                    _RATELIMIT_CHANNEL.name,
                    self._subscriber_id,
                    timeout=_POLL_TIMEOUT,
                )
                if envelope is None:
                    continue
                await self._ingest(envelope.message)
                await envelope.ack()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # A receive/ingest failure means this worker is no
                # longer seeing remote acquires. Flip ``_distributed``
                # to ``False`` so ``acquire()`` stops assuming the
                # global window is synchronized -- otherwise we would
                # keep issuing requests under a fraction of the
                # global cap and silently under-enforce the shared
                # limit. Exiting the loop here also stops burning
                # a second retry on every poll timeout.
                self._distributed = False
                logger.warning(
                    RATE_LIMIT_COORDINATOR_STARTED,
                    connection_name=self._connection_name,
                    subscriber_id=self._subscriber_id,
                    error=(
                        "poll loop error; falling back to local-only "
                        "coordination and exiting poll loop"
                    ),
                )
                return

    async def _ingest(self, message: object) -> None:
        """Update local window from a remote acquire event."""
        from synthorg.communication.message import (  # noqa: PLC0415
            Message as Msg,
        )

        if not isinstance(message, Msg):
            return
        for part in message.parts:
            if not isinstance(part, DataPart):
                continue
            data = dict(part.data) if part.data is not None else {}
            if data.get("connection_name") != self._connection_name:
                continue
            ts = data.get("timestamp")
            if isinstance(ts, int | float):
                async with self._window_lock:
                    self._window.append(float(ts))
                    self._evict_old(self._clock.now().timestamp())


_coordinator_factory: Callable[[str], SharedRateLimitCoordinator] | None = None
_coordinators: dict[str, SharedRateLimitCoordinator] = {}

_COORDINATOR_LOCK: Final[threading.Lock] = threading.Lock()


async def set_coordinator_factory(
    factory: Callable[[str], SharedRateLimitCoordinator] | None,
) -> None:
    """Set (or clear) the factory used to create coordinators.

    Called from ``auto_wire.py`` after the bus and catalog exist.
    Any previously-created coordinators are stopped so background
    poll tasks produced by the old factory don't linger.

    Args:
        factory: New factory callable, or ``None`` to disable.
    """
    global _coordinator_factory  # noqa: PLW0603
    # Snapshot, clear, and swap the factory atomically so a concurrent
    # ``get_coordinator()`` cannot insert into the dict between the
    # snapshot and the clear, nor build a fresh coordinator against
    # the outgoing factory after the swap should have taken effect.
    # The ``await coordinator.stop()`` calls happen outside the lock
    # because ``stop()`` itself takes the coordinator's own asyncio
    # locks and the threading.Lock here must not be held across an
    # ``await`` (it would block every other in-process consumer of
    # the same lock while the event loop yields).
    with _COORDINATOR_LOCK:
        old = tuple(_coordinators.values())
        _coordinators.clear()
        _coordinator_factory = factory
    for coordinator in old:
        try:
            await coordinator.stop()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RATE_LIMIT_COORDINATOR_STOPPED,
                connection_name=coordinator._connection_name,  # noqa: SLF001
                phase="factory_swap_stop_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


def register_coordinator_factory(
    factory: Callable[[str], SharedRateLimitCoordinator] | None,
) -> None:
    """Register the coordinator factory without tearing down old coordinators.

    Use only in startup paths where no coordinators have been
    created yet. The async ``set_coordinator_factory`` should be
    preferred when re-wiring after the first acquire.
    """
    global _coordinator_factory  # noqa: PLW0603
    with _COORDINATOR_LOCK:
        _coordinator_factory = factory


def get_coordinator(connection_name: str) -> SharedRateLimitCoordinator | None:
    """Get or create a coordinator for the given connection.

    The check-then-set on ``_coordinators`` and the read of
    ``_coordinator_factory`` are both protected by the lock so a
    concurrent factory swap (via :func:`set_coordinator_factory` or
    :func:`register_coordinator_factory`) cannot leave us calling a
    stale or ``None`` factory between the existence check and the
    invocation.

    Returns:
        The existing or newly-created ``SharedRateLimitCoordinator`` for
        the connection, or ``None`` when no factory is registered.
    """
    with _COORDINATOR_LOCK:
        if _coordinator_factory is None:
            return None
        if connection_name not in _coordinators:
            _coordinators[connection_name] = _coordinator_factory(connection_name)
        return _coordinators[connection_name]
