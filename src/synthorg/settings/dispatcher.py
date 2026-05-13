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
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_CHANNEL_CREATED,
    SETTINGS_DISPATCHER_CHANNEL_DEAD,
    SETTINGS_DISPATCHER_POLL_ERROR,
    SETTINGS_DISPATCHER_RESOLVE_FAILED,
    SETTINGS_DISPATCHER_START_REJECTED,
    SETTINGS_DISPATCHER_STARTED,
    SETTINGS_DISPATCHER_STOP_FAILED,
    SETTINGS_DISPATCHER_STOPPED,
    SETTINGS_SUBSCRIBER_ERROR,
    SETTINGS_SUBSCRIBER_NOTIFIED,
    SETTINGS_SUBSCRIBER_RESTART_REQUIRED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.subscriber import SettingsSubscriber  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.communication.message import Message
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_SUBSCRIBER_ID: Final[str] = "__settings_dispatcher__"
_POLL_TIMEOUT: Final[float] = 1.0
"""Bootstrap poll timeout used before the settings resolver is ready."""
_ERROR_BACKOFF: Final[float] = 1.0
"""Bootstrap error backoff used before the settings resolver is ready."""
_SETTINGS_CHANNEL: Final[str] = "#settings"


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
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._bus = message_bus
        self._subscribers = subscribers
        # Optional kill switch surface. ``None`` is the bootstrap
        # default for tests / first-pump cycles before the settings
        # service is wired; the dispatcher then runs unconditionally
        # (fail-safe to enabled). Production wires the resolver via
        # ``lifecycle_helpers.build_settings_change_dispatcher`` so
        # operators can flip ``settings.dispatcher.enabled`` to pause
        # propagation without restarting.
        self._config_resolver: ConfigResolver | None = config_resolver
        self._resolve_failed_logged: bool = False
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
        # Initialised lazily in ``start()`` so the Lock binds to the
        # event loop that actually drives the dispatcher; eager
        # init in __init__ pins the Lock to whichever loop happens
        # to be current at construction time, which breaks restart
        # across pytest-asyncio's per-test loops or any production
        # scenario where construction and start() run on different
        # loops. Canonical pattern: see ApprovalTimeoutScheduler.
        self._lifecycle_lock: asyncio.Lock | None = None
        # Strong refs to the lock-acquiring fire-and-forget tasks
        # spawned from ``_on_task_done`` (RUF006). The callback runs
        # synchronously and cannot ``await`` the lock, so it
        # schedules the state update via ``create_task``; without a
        # strong ref the task could be garbage-collected mid-flight.
        self._post_done_tasks: set[asyncio.Task[None]] = set()

    def _task_is_on_current_loop(self) -> bool:
        """True iff a cross-loop drop of lifecycle primitives is NOT warranted.

        Used internally by ``start()`` to detect cross-loop reuse.
        Returns ``True`` (i.e. "do not drop state") when:

        * No task exists yet (nothing to drop).
        * The task is done -- a finished prior lifecycle is not the
          same as cross-loop reuse, and a concurrent ``stop()`` may
          still be holding the lock to drain. Diverges from the
          ApprovalTimeoutScheduler canonical here because the
          settings dispatcher has a separate ``_running`` flag that
          coordinates with ``stop()``; dropping the lock under a
          live ``stop()`` would replace it with a fresh lock and
          break that serialisation. The done-but-stale state is
          cleaned up inside the lock by
          :meth:`_collapse_finished_task_under_lock` instead.
        * The task or running loop cannot be introspected --
          typically a ``MagicMock(spec=asyncio.Task)`` in tests.
        """
        if self._task is None or self._task.done():
            return True
        try:
            task_loop: object = self._task.get_loop()
        except RuntimeError, AttributeError:
            return True
        if not isinstance(task_loop, asyncio.AbstractEventLoop):
            return True
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return True
        return task_loop is current

    def _drop_stale_loop_state(self) -> None:
        """Discard task / lifecycle lock bound to a closed-or-other loop."""
        self._task = None
        self._lifecycle_lock = None

    async def _collapse_finished_task_under_lock(self) -> None:
        """Recover lifecycle state when the prior poll task crashed.

        Called from inside ``start()``'s ``_lifecycle_lock`` to close
        the window between the synchronous ``_on_task_done`` callback
        firing and ``_reset_running_under_lock`` actually acquiring
        the lock.  Also unsubscribes the dead task's bus registration
        because only ``stop()`` and the spawn-rollback path call
        ``unsubscribe`` today; a crash leaves
        ``__settings_dispatcher__`` registered on ``#settings``, and
        the subscribe() further down would then double-register on
        bus implementations whose subscribe is not idempotent
        (NATS in particular).
        """
        if self._task is None or not self._task.done():
            return
        try:
            await self._bus.unsubscribe(
                _SETTINGS_CHANNEL,
                _SUBSCRIBER_ID,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Recovery requires the stale registration to be gone
            # before subscribe() runs further down start(). Continuing
            # past this point on a non-idempotent bus (NATS) would
            # leave two ``__settings_dispatcher__`` registrations
            # alive and double-deliver every settings change -- the
            # exact corruption mode this helper exists to prevent.
            # Mark the dispatcher unrestartable and re-raise so
            # start() exits before subscribe() runs again.
            self._running = False
            self._task = None
            self._stop_failed = True
            logger.error(
                SETTINGS_DISPATCHER_START_REJECTED,
                error=(
                    "unsubscribe of crashed-task registration "
                    "failed during start() recovery"
                ),
                reason="recovery_unsubscribe_failed",
                error_type=type(exc).__name__,
                error_description=safe_error_description(exc),
            )
            msg = (
                "start() recovery could not remove the stale settings "
                "subscription; dispatcher marked unrestartable, "
                "construct a fresh dispatcher instead"
            )
            raise RuntimeError(msg) from exc
        self._running = False
        self._task = None

    async def start(self) -> None:
        """Start the polling loop.

        Raises:
            RuntimeError: If the dispatcher is already running.
        """
        # Detect cross-loop reuse before touching any lifecycle
        # primitive. Otherwise ``async with self._lifecycle_lock``
        # would itself raise ``<Lock> is bound to a different event
        # loop`` on the FIRST line of the function and there is
        # nothing the dispatcher can do to recover after that.
        if self._task is not None and not self._task_is_on_current_loop():
            self._drop_stale_loop_state()
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
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
            await self._collapse_finished_task_under_lock()
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
        ``_task`` not yet assigned).  No-op if ``start()`` was never
        called -- the lifecycle lock is constructed lazily on first
        ``start()``, so a missing lock means there is nothing to drain.
        """
        if self._lifecycle_lock is None:
            return
        async with self._lifecycle_lock:
            if not self._running:
                return

            if self._task is not None:
                drain_timeout = await self._resolve_stop_drain_timeout()
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
                        timeout=drain_timeout,
                    )
                except TimeoutError as exc:
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
                        SETTINGS_DISPATCHER_STOP_FAILED,
                        note=(
                            "stop exceeded hard deadline; "
                            "dispatcher marked unrestartable"
                        ),
                        timeout_seconds=drain_timeout,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
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
                    # in an inconsistent state. ``_task`` may have
                    # been cleared by ``_reset_running_under_lock``
                    # racing in under a previous lifecycle-lock
                    # holder; treat that as "task completed" since
                    # the reset only fires after the task is done.
                    if self._task is not None and not self._task.done():
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
                    SETTINGS_DISPATCHER_STOP_FAILED,
                    note=(
                        "clean-stop unsubscribe failed; dispatcher marked unrestartable"
                    ),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

            self._running = False
            logger.info(SETTINGS_DISPATCHER_STOPPED)
        # Drop the lifecycle lock outside the ``async with`` block so
        # the next ``start()`` (potentially on a different event loop --
        # e.g. pytest-asyncio's per-test loop, or a process that tore
        # down the prior loop and rebuilt one) constructs a fresh
        # ``asyncio.Lock`` bound to the new loop. Without this clear,
        # ``async with self._lifecycle_lock:`` in the next start()
        # would raise ``RuntimeError: <Lock> is bound to a different
        # event loop`` since the cross-loop guard only fires when
        # ``self._task`` is non-None and stop() has just set it to
        # None on the way through.
        self._lifecycle_lock = None

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Handle unexpected poll-loop exit.

        The poll task ending while ``stop()`` is not driving the
        teardown means the loop crashed (or hit max-consecutive-errors)
        and the dispatcher needs ``_running`` reset so a subsequent
        ``start()`` can proceed without a manual ``stop()`` first.
        The callback runs synchronously on the event loop and cannot
        ``await`` the ``_lifecycle_lock``, so the locked update is
        scheduled as a follow-up coroutine; logging happens here
        immediately so a crashed loop is observable even if the
        scheduled task is delayed.
        """
        if task.cancelled():
            return
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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. interpreter shutdown). The
            # ``_running`` flag stays True; the dispatcher is being
            # torn down anyway and a future start() on a fresh
            # instance is the recovery path.
            return
        post_task = loop.create_task(
            self._reset_running_under_lock(task),
            name="settings-dispatcher-post-done",
        )
        self._post_done_tasks.add(post_task)
        # Discard the strong ref when the task finishes, so the set
        # does not hold a reference indefinitely. Without this callback,
        # the set would keep the task alive forever, defeating RUF006
        # protection (the whole point of storing strong refs here).
        post_task.add_done_callback(self._post_done_tasks.discard)

    async def _reset_running_under_lock(self, task: asyncio.Task[None]) -> None:
        """Clear ``_running`` under the lifecycle lock after a crash.

        Acquiring the lock serialises this write against concurrent
        ``start()`` / ``stop()`` calls so a crashed-task callback
        cannot race a fresh ``start()`` into observing
        ``_running=False`` while the previous task object is still
        bound to ``self._task``.  When ``start()`` has already
        cleared the lock (cross-loop drop, dispatcher reconstruction)
        the callback degrades to a best-effort write since there is
        no longer a lock to acquire.
        """
        if self._lifecycle_lock is None:
            if self._task is task:
                self._running = False
                self._task = None
            return
        async with self._lifecycle_lock:
            if self._task is task:
                self._running = False
                self._task = None

    async def _resolve_enabled(self) -> bool:
        """Resolve the kill-switch flag, fail-safe to ``True``.

        Operators flip ``settings.dispatcher.enabled=false`` to pause
        the propagation loop without tearing down subscribers. A
        settings-backend outage must not silently silence the
        dispatcher (the operator is the only sanctioned silencer), so
        any resolver failure resolves to enabled. The first failure
        per run logs a WARNING; the surface re-arms on the next
        successful resolve so a transient outage does not fill the
        log with duplicates.
        """
        if self._config_resolver is None:
            return True
        try:
            value = await self._config_resolver.get_bool(
                SettingNamespace.SETTINGS.value, "dispatcher_enabled"
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            if not self._resolve_failed_logged:
                logger.warning(
                    SETTINGS_DISPATCHER_RESOLVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                self._resolve_failed_logged = True
            return True
        self._resolve_failed_logged = False
        return value

    async def _resolve_max_consecutive_errors(self) -> int:
        """Resolve the consecutive-error budget; bootstrap fallback is 30.

        Read each loop iteration so an operator can lower / raise the
        budget without a dispatcher restart. Resolver outage falls
        back to the bootstrap default so the loop keeps pumping with
        a sane default rather than aborting on the first error.

        The bootstrap literal (30) duplicates the registered default
        in ``settings/definitions/settings_ns.py``
        (``dispatcher_max_consecutive_errors``); kept inline as a
        literal because importing the registry value at module-load
        risks a circular import (registry depends on settings models;
        settings models depend on enums; the dispatcher module is on
        the resolution path back). Keep both in lockstep when
        adjusting the registered default.
        """
        bootstrap_default = 30
        if self._config_resolver is None:
            return bootstrap_default
        try:
            return await self._config_resolver.get_int(
                SettingNamespace.SETTINGS.value, "dispatcher_max_consecutive_errors"
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception:
            return bootstrap_default

    async def _resolve_stop_drain_timeout(self) -> float:
        """Resolve the stop() drain hard deadline; bootstrap fallback is 10.0s.

        Read once at stop() entry so an operator can extend the
        deadline ahead of a planned drain without code changes.
        Resolver outage falls back to the bootstrap default so the
        drain still bounds the lifecycle lock.

        The bootstrap literal (10.0) duplicates the registered
        default in ``settings/definitions/settings_ns.py``
        (``dispatcher_stop_drain_timeout_seconds``); kept inline
        for the same circular-import reason described on
        ``_resolve_max_consecutive_errors``. Keep both in lockstep
        when adjusting the registered default.
        """
        bootstrap_default = 10.0
        if self._config_resolver is None:
            return bootstrap_default
        try:
            return await self._config_resolver.get_float(
                SettingNamespace.SETTINGS.value,
                "dispatcher_stop_drain_timeout_seconds",
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception:
            return bootstrap_default

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
            # Resolve the kill switch as the first top-level statement
            # inside the loop body so the long-running-loops gate can
            # see the guard. Operator paused dispatch via the flag;
            # sleep the same poll-timeout we'd otherwise spend in
            # ``bus.receive`` so the loop yields the event loop at the
            # same cadence and the flip takes effect within one tick.
            if not await self._resolve_enabled():
                await asyncio.sleep(_POLL_TIMEOUT)
                continue
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
                await envelope.ack()
            except asyncio.CancelledError:
                raise
            except MemoryError, RecursionError:
                raise
            except (OSError, TimeoutError) as exc:
                consecutive_errors += 1
                max_errors = await self._resolve_max_consecutive_errors()
                if consecutive_errors >= max_errors:
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
    restart_required = not compare_ci(str(restart_raw), "false")
    return _ChangeMetadata(namespace, key, restart_required)
