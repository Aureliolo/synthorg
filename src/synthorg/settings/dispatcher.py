"""Settings change dispatcher -- polls ``#settings`` and routes to subscribers.

Follows the same polling-loop pattern as
:class:`~synthorg.api.bus_bridge.MessageBusBridge`.
"""

import asyncio
from typing import TYPE_CHECKING, Final, NamedTuple

from synthorg.communication.bus_protocol import MessageBus  # noqa: TC001
from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType
from synthorg.communication.errors import ChannelAlreadyExistsError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_CHANNEL_CREATED,
    SETTINGS_DISPATCHER_CHANNEL_DEAD,
    SETTINGS_DISPATCHER_POLL_ERROR,
    SETTINGS_DISPATCHER_START_REJECTED,
    SETTINGS_DISPATCHER_STARTED,
    SETTINGS_DISPATCHER_STOPPED,
    SETTINGS_SUBSCRIBER_ERROR,
    SETTINGS_SUBSCRIBER_NOTIFIED,
    SETTINGS_SUBSCRIBER_RESTART_REQUIRED,
)
from synthorg.settings.subscriber import SettingsSubscriber  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.communication.message import Message

logger = get_logger(__name__)

_SUBSCRIBER_ID: Final[str] = "__settings_dispatcher__"
_BOOTSTRAP_POLL_TIMEOUT: Final[float] = 1.0
"""Fallback poll timeout used before the settings resolver is ready."""
_BOOTSTRAP_ERROR_BACKOFF: Final[float] = 1.0
"""Fallback error backoff used before the settings resolver is ready."""
_BOOTSTRAP_MAX_CONSECUTIVE_ERRORS: Final[int] = 30
"""Fallback error budget used before the settings resolver is ready."""
_SETTINGS_CHANNEL: Final[str] = "#settings"
_STOP_DRAIN_TIMEOUT_SECONDS: Final[float] = 10.0
"""Hard deadline for the ``stop()`` drain.

Per CLAUDE.md ``## Code Conventions`` > Lifecycle synchronization:
services whose ``stop()`` drains across ``await`` boundaries must
wrap the drain in ``asyncio.wait_for`` so the lifecycle lock cannot
be held indefinitely if the polling task ignores cancellation.
"""

# Legacy aliases (retain name-compat for callers reaching into this module).
_POLL_TIMEOUT = _BOOTSTRAP_POLL_TIMEOUT
_ERROR_BACKOFF = _BOOTSTRAP_ERROR_BACKOFF
_MAX_CONSECUTIVE_ERRORS = _BOOTSTRAP_MAX_CONSECUTIVE_ERRORS


class _ChangeMetadata(NamedTuple):
    """Structured metadata extracted from a ``#settings`` bus message."""

    namespace: str
    key: str
    restart_required: bool


class SettingsChangeDispatcher:
    """Dispatch ``#settings`` bus messages to registered subscribers.

    On ``start()``, subscribes to the ``#settings`` channel and
    begins polling for change notifications published by
    :class:`~synthorg.settings.service.SettingsService`.

    Each incoming message is matched against subscribers'
    ``watched_keys``.  For settings with ``restart_required=True``,
    a WARNING is logged and subscribers are **not** called.  For all
    other settings, matching subscribers' ``on_settings_changed``
    is invoked.  Errors in individual subscribers are logged and
    swallowed -- the poll loop is never interrupted.

    Args:
        message_bus: The message bus to poll.
        subscribers: Registered settings subscribers.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        subscribers: tuple[SettingsSubscriber, ...],
    ) -> None:
        self._bus = message_bus
        self._subscribers = subscribers
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False
        # Set to True when a stop() drain exceeds the hard deadline.
        # Prevents a subsequent start() from spawning a second poll
        # task while the first one is still consuming ``#settings``
        # (would double-deliver every settings change to each
        # subscriber). Recovery requires reconstructing the dispatcher.
        self._stop_failed: bool = False
        # Serializes start() / stop() so the _running check-and-set
        # and the subsequent _task assignment are atomic against
        # concurrent lifecycle calls. Two concurrent start() calls
        # both observing _running=False would otherwise both
        # subscribe to #settings and both spawn a poll task.
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the polling loop.

        Raises:
            RuntimeError: If the dispatcher is already running.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "SettingsChangeDispatcher is unrestartable after a "
                    "timed-out stop; construct a fresh dispatcher instead"
                )
                # Dedicated rejection event -- do not reuse
                # SETTINGS_DISPATCHER_STARTED, which would inflate
                # successful-start metrics/alerts.
                logger.warning(
                    SETTINGS_DISPATCHER_START_REJECTED,
                    error=msg,
                    reason="unrestartable",
                )
                raise RuntimeError(msg)
            if self._running:
                msg = "SettingsChangeDispatcher is already running"
                logger.warning(
                    SETTINGS_DISPATCHER_START_REJECTED,
                    error=msg,
                    reason="already_running",
                )
                raise RuntimeError(msg)

            # Pre-spawn failures (channel ensure / bus subscribe) are
            # a distinct lifecycle error path from the spawn-rollback
            # below. Log SETTINGS_DISPATCHER_START_REJECTED so both
            # failure modes surface in observability; without this the
            # pre-spawn path would leak the exception without a
            # dispatcher-specific event.
            try:
                await self._ensure_channel()
                # Subscribe + spawn must be transactional: if
                # subscribe() succeeds but the task spawn (or any
                # subsequent step that could be added later) raises,
                # we must roll back the subscription so a retried
                # start() does not double-subscribe and stop() does
                # not silently skip cleanup (stop() early-returns on
                # ``_running=False``).
                await self._bus.subscribe(_SETTINGS_CHANNEL, _SUBSCRIBER_ID)
            except Exception:
                logger.warning(
                    SETTINGS_DISPATCHER_START_REJECTED,
                    error="channel ensure/subscribe failed during start()",
                    reason="subscribe_failed",
                )
                raise
            try:
                self._running = True
                self._task = asyncio.create_task(
                    self._poll_loop(),
                    name="settings-dispatcher",
                )
                self._task.add_done_callback(self._on_task_done)
            except BaseException:
                self._running = False
                self._task = None
                try:
                    await self._bus.unsubscribe(
                        _SETTINGS_CHANNEL,
                        _SUBSCRIBER_ID,
                    )
                except Exception:
                    # Best-effort rollback -- a failed unsubscribe
                    # during already-failed start() leaves the bus with
                    # a stale ``__settings_dispatcher__`` registration
                    # on ``#settings``. Mark the dispatcher as
                    # half-stopped (``_running=True`` + ``_stop_failed
                    # =True``) so a subsequent ``stop()`` still runs
                    # the clean-stop unsubscribe instead of early-
                    # returning on ``_running=False`` and leaking the
                    # subscription. The original start() exception is
                    # still raised below.
                    self._running = True
                    self._stop_failed = True
                    logger.warning(
                        SETTINGS_DISPATCHER_START_REJECTED,
                        error="rollback unsubscribe failed during start() cleanup",
                        reason="rollback_unsubscribe_failed",
                    )
                raise
            logger.info(
                SETTINGS_DISPATCHER_STARTED,
                subscriber_count=len(self._subscribers),
            )

    async def stop(self) -> None:
        """Cancel the polling task.  Idempotent.

        Holds ``_lifecycle_lock`` so ``stop()`` cannot race a
        partially-constructed ``start()`` (e.g. channel subscribed but
        ``_task`` not yet assigned).
        """
        async with self._lifecycle_lock:
            if not self._running:
                return

            if self._task is not None:
                self._task.cancel()
                try:
                    # ``asyncio.shield`` guarantees the hard deadline
                    # applies to the wait only, not to the underlying
                    # task. Without the shield, a poll task that
                    # swallows ``CancelledError`` would keep the outer
                    # ``wait_for`` blocked inside ``_lifecycle_lock``
                    # forever; the shield lets the wait time out so
                    # ``stop()`` can release the lock and mark the
                    # dispatcher unrestartable even if the task itself
                    # refuses to exit.
                    await asyncio.wait_for(
                        asyncio.shield(self._task),
                        timeout=_STOP_DRAIN_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    # Drain exceeded the hard deadline. Mark the
                    # dispatcher unrestartable and re-raise: a future
                    # start() must not spawn a second poll task
                    # alongside an orphaned one that ignored
                    # cancellation (would double-deliver every
                    # #settings message to each subscriber). Leave
                    # ``_task`` + ``_running`` intact so the caller
                    # sees an honest incomplete shutdown; they must
                    # reconstruct the dispatcher to recover.
                    self._stop_failed = True
                    # TRY400: logger.exception here would append a
                    # TimeoutError traceback with no actionable
                    # diagnostic beyond the structured fields below.
                    logger.error(
                        SETTINGS_DISPATCHER_STOPPED,
                        error=(
                            "stop exceeded hard deadline; "
                            "dispatcher marked unrestartable"
                        ),
                        timeout_seconds=_STOP_DRAIN_TIMEOUT_SECONDS,
                    )
                    raise
                except asyncio.CancelledError:
                    # Only suppress when the cancellation came from
                    # the poll task completing (expected). If the
                    # task is still running, the CancelledError came
                    # from the outer caller cancelling ``stop()``;
                    # propagate it so lifecycle state does not get
                    # silently cleared mid-drain. Suppressing caller
                    # cancellation would violate the asyncio
                    # cancellation contract and leave the dispatcher
                    # in an inconsistent state.
                    if not self._task.done():
                        raise
                self._task = None

            # Clean-stop path must mirror the rollback unsubscribe so
            # the bus does not keep ``__settings_dispatcher__``
            # registered on ``#settings`` across stop/start cycles.
            # Without this, the next ``start()`` would re-enter
            # ``_bus.subscribe`` for an already-registered subscriber
            # (idempotent on the in-memory bus but not necessarily on
            # the NATS bus), and the stopped dispatcher would still be
            # buffering ``#settings`` messages in the bus's per-sub
            # queue until channel cleanup.
            try:
                await self._bus.unsubscribe(_SETTINGS_CHANNEL, _SUBSCRIBER_ID)
            except Exception:
                # Unsubscribe failure means the bus still holds a
                # stale ``__settings_dispatcher__`` registration on
                # ``#settings``. Mark the dispatcher unrestartable so
                # a retry on the same instance does not double-
                # subscribe (the operator must reconstruct the
                # dispatcher to recover). Leave ``_running`` at True
                # so a subsequent ``stop()`` still runs the clean-
                # stop unsubscribe instead of early-returning.
                self._stop_failed = True
                logger.error(
                    SETTINGS_DISPATCHER_STOPPED,
                    error=(
                        "clean-stop unsubscribe failed; dispatcher marked unrestartable"
                    ),
                )
                raise

            self._running = False
            logger.info(SETTINGS_DISPATCHER_STOPPED)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Handle unexpected poll-loop exit.

        Resets ``_running`` so the dispatcher's state is honest,
        and logs an error if the loop died with an exception.
        """
        if task.cancelled():
            return
        self._running = False
        exc = task.exception()
        if exc is not None:
            logger.error(
                SETTINGS_DISPATCHER_CHANNEL_DEAD,
                note="Settings dispatcher poll loop died unexpectedly",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            logger.warning(
                SETTINGS_DISPATCHER_STOPPED,
                note="Poll loop exited (max consecutive errors or channel dead)",
            )

    async def _ensure_channel(self) -> None:
        """Create ``#settings`` channel if it does not exist."""
        try:
            await self._bus.create_channel(
                Channel(name=_SETTINGS_CHANNEL, type=ChannelType.TOPIC),
            )
            logger.debug(SETTINGS_CHANNEL_CREATED, channel=_SETTINGS_CHANNEL)
        except ChannelAlreadyExistsError:
            pass

    async def _poll_loop(self) -> None:
        """Continuously poll ``#settings`` and dispatch to subscribers."""
        consecutive_errors = 0

        while True:
            try:
                envelope = await self._bus.receive(
                    _SETTINGS_CHANNEL,
                    _SUBSCRIBER_ID,
                    timeout=_POLL_TIMEOUT,
                )
                if envelope is None:
                    continue
                consecutive_errors = 0
                await self._dispatch(envelope.message)
            except asyncio.CancelledError:
                raise
            except MemoryError, RecursionError:
                raise
            except (OSError, TimeoutError) as exc:
                consecutive_errors += 1
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        SETTINGS_DISPATCHER_CHANNEL_DEAD,
                        consecutive_errors=consecutive_errors,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    break
                logger.warning(
                    SETTINGS_DISPATCHER_POLL_ERROR,
                    consecutive_errors=consecutive_errors,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                await asyncio.sleep(_ERROR_BACKOFF)
            except Exception as exc:
                logger.error(
                    SETTINGS_DISPATCHER_CHANNEL_DEAD,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                break

    async def _dispatch(self, message: Message) -> None:
        """Route a single settings change to matching subscribers."""
        meta = _extract_metadata(message)
        if meta is None:
            return

        namespace, key, restart_required = meta

        if restart_required:
            logger.warning(
                SETTINGS_SUBSCRIBER_RESTART_REQUIRED,
                namespace=namespace,
                key=key,
            )
            return

        for subscriber in self._subscribers:
            try:
                if (namespace, key) not in subscriber.watched_keys:
                    continue
                await subscriber.on_settings_changed(namespace, key)
                logger.info(
                    SETTINGS_SUBSCRIBER_NOTIFIED,
                    subscriber=subscriber.subscriber_name,
                    namespace=namespace,
                    key=key,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.error(
                    SETTINGS_SUBSCRIBER_ERROR,
                    subscriber=getattr(subscriber, "subscriber_name", "unknown"),
                    namespace=namespace,
                    key=key,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )


def _extract_metadata(
    message: Message,
) -> _ChangeMetadata | None:
    """Extract structured change metadata from a ``#settings`` message.

    Returns:
        A :class:`_ChangeMetadata` or ``None`` if the ``namespace`` or
        ``key`` metadata fields are missing.  The ``restart_required``
        field defaults to ``True`` when absent -- fail-safe to prevent
        accidental hot-reload of restart-required settings on metadata
        corruption.
    """
    extra = dict(message.metadata.extra)
    namespace = extra.get("namespace")
    key = extra.get("key")
    if namespace is None or key is None:
        logger.warning(
            SETTINGS_DISPATCHER_POLL_ERROR,
            error="Received #settings message with missing metadata",
            has_namespace=namespace is not None,
            has_key=key is not None,
            sender=message.sender,
        )
        return None
    # restart_required is encoded as str(bool) by SettingsService._publish_change.
    # Default to True (fail-safe): missing/corrupted metadata prevents hot-reload
    # rather than accidentally allowing it for restart-required settings.
    restart_raw = extra.get("restart_required", "True")
    restart_required = str(restart_raw).lower() != "false"
    return _ChangeMetadata(namespace, key, restart_required)
