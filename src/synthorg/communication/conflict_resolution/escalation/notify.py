"""Cross-instance wake-up for the human escalation queue.

When the escalation queue runs on a shared database (currently Postgres)
and the API is deployed across multiple workers/pods, a resolver
awaiting a Future on worker A must be woken when an operator submits a
decision through worker B.  The Future itself is process-local
(:class:`PendingFuturesRegistry`), so the wake signal has to travel
through the shared database.

This module provides the :class:`EscalationNotifySubscriber` abstract
contract and a Postgres implementation that subscribes to a LISTEN
channel populated by triggers on the ``conflict_escalations`` table.
SQLite/in-memory backends have no cross-instance concern, so the
factory returns a :class:`NoopEscalationNotifySubscriber`.
"""

import asyncio
import contextlib
import re
from typing import Final, Protocol, runtime_checkable

from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
    CONFLICT_ESCALATION_SUBSCRIBER_PAUSED,
    CONFLICT_ESCALATION_SUBSCRIBER_STARTED,
    CONFLICT_ESCALATION_SUBSCRIBER_STOPPED,
)
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

# Safe Postgres unquoted-identifier pattern.  Defence-in-depth: the
# config layer validates this too, but the subscriber re-checks so a
# hand-constructed subscriber cannot inject unsafe SQL via
# ``LISTEN "<channel>"``.
_SAFE_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$",
)
_MAX_IDENTIFIER_LEN: Final[int] = 63


@runtime_checkable
class EscalationNotifySubscriber(Protocol):
    """Contract for cross-instance escalation wake-up subscribers.

    Implementations listen on a backend-specific signal (Postgres
    LISTEN/NOTIFY, NATS subjects, etc.) and forward state transitions
    to an in-process :class:`PendingFuturesRegistry` so any local
    resolver awaiting the escalation wakes with the correct payload.
    """

    async def start(self) -> None:
        """Begin subscribing.  Must be idempotent."""
        ...

    async def stop(self) -> None:
        """Stop subscribing and release resources.  Must be idempotent."""
        ...

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Inject the ConfigResolver after construction.

        The auto-wire startup path builds the subscriber before the
        resolver is available; the API lifecycle hook calls this once
        the resolver is ready so live kill-switch reads are honoured.
        """
        ...


class NoopEscalationNotifySubscriber:
    """No-op subscriber for single-worker / in-memory deployments."""

    async def start(self) -> None:
        """Noop."""
        return

    async def stop(self) -> None:
        """Noop."""
        return

    def set_config_resolver(self, resolver: ConfigResolver) -> None:  # noqa: ARG002
        """Noop -- the no-op subscriber has no kill-switch to gate."""
        return


class PostgresEscalationNotifySubscriber:
    """Subscribes to a Postgres LISTEN channel and wakes local futures.

    The Postgres ``conflict_escalations`` schema installs triggers that
    ``NOTIFY`` on the configured channel whenever a row transitions out
    of PENDING.  This subscriber fans those notifications out to the
    local :class:`PendingFuturesRegistry`: DECIDED rows cause
    ``registry.resolve`` (with the decision payload read from the row);
    EXPIRED/CANCELLED rows cause ``registry.cancel`` so any local
    resolver awaiting the Future is promptly unblocked.

    The subscriber is best-effort: connection failures are logged and
    the loop reconnects with a short back-off, never propagating to the
    application.  Missing a signal is not catastrophic because each
    resolver has its own ``timeout_seconds`` deadline and the
    :class:`EscalationExpirationSweeper` eventually reaps stale rows.
    """

    def __init__(  # noqa: PLR0913 -- subscriber wiring (positional-only refactor would be churn)
        self,
        repo: EscalationQueueStore,
        registry: PendingFuturesRegistry,
        *,
        channel: str,
        reconnect_delay_seconds: float,
        config_resolver: ConfigResolver | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the subscriber.

        Args:
            repo: Escalation queue store (protocol-typed).  Postgres
                implementations deliver real NOTIFY payloads via
                ``subscribe_notifications``; other backends either
                return a backend-specific stream or raise
                ``RuntimeError`` from ``subscribe_notifications`` when
                subscriptions are unsupported (see the contract in
                ``synthorg.communication.conflict_resolution.escalation.protocol``).
            registry: Process-local registry whose futures should wake.
            channel: LISTEN/NOTIFY channel name.
            reconnect_delay_seconds: Seconds to wait before reconnecting
                after a connection failure.  Must be positive.  Resolve
                via ``ConfigResolver.get_float("communication",
                "escalation_subscriber_reconnect_delay_seconds")`` at
                the call site.
            config_resolver: Optional resolver for the
                ``communication.escalation_notify_subscriber_enabled``
                kill-switch.  When wired the loop body re-reads the
                flag every iteration so an operator can pause the
                subscriber at runtime; without a resolver the loop
                runs unconditionally (matches the registered default).
            clock: Time seam per ``CLAUDE.md``.  Defaults to
                :class:`SystemClock` so production keeps wall-clock
                semantics; tests inject ``FakeClock`` to drive the
                reconnect back-off and ``stop()`` drain deadline on
                virtual time.

        Raises:
            ValueError: If ``reconnect_delay_seconds`` is not positive or
                the channel name is unsafe.
        """
        if reconnect_delay_seconds <= 0:
            msg = "reconnect_delay_seconds must be > 0"
            raise ValueError(msg)
        # Defensive: config.py already validates the channel, but a
        # hand-constructed subscriber must not be able to inject SQL
        # via ``LISTEN "<channel>"``.
        if (
            not channel
            or len(channel) > _MAX_IDENTIFIER_LEN
            or _SAFE_IDENTIFIER_PATTERN.fullmatch(channel) is None
        ):
            msg = (
                f"notify channel {channel!r} is not a safe Postgres identifier "
                "(must match ^[A-Za-z_][A-Za-z0-9_]*$, max 63 chars)"
            )
            raise ValueError(msg)
        self._repo = repo
        self._registry = registry
        self._channel = channel
        self._reconnect_delay = reconnect_delay_seconds
        self._config_resolver = config_resolver
        # ``Clock`` seam per ``CLAUDE.md`` -- tests inject ``FakeClock``
        # so the reconnect back-off and stop() drain deadline run on
        # virtual time instead of wall time.
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._task: asyncio.Task[None] | None = None
        # Eager construction of the lifecycle primitives. Python 3.10+
        # ``asyncio.Lock`` / ``asyncio.Event`` are loop-agnostic until
        # first ``acquire()`` / ``set()``, so constructing them at
        # app-wire time (no loop yet) is safe. Lazy-creating them
        # inside ``start()`` would publish the lock attribute *before*
        # the ``async with`` body ran, letting a racing ``stop()``
        # observe a fresh lock instance and operate on different
        # primitives than the in-flight ``start()``.
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see above.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Per ``docs/reference/lifecycle-sync.md``: a ``stop()`` drain
        # that exceeds the hard deadline marks the subscriber
        # unrestartable so a subsequent ``start()`` cannot attach a
        # fresh task while the orphan loop still holds the LISTEN
        # connection. The flag survives any state resets so it
        # remains observable on the next ``start()`` call.
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Inject the ConfigResolver after construction.

        ``EscalationNotifySubscriber`` is created at app-wire time
        (:func:`synthorg.api.app.create_app`), but in the auto-wire
        startup path ``app_state.config_resolver`` is not yet available
        at that moment. The startup hook calls this setter after the
        resolver is built and before :meth:`start` so the loop body's
        ``communication.escalation_notify_subscriber_enabled``
        kill-switch reads honour the live operator-tuned value instead
        of falling through to the registered default.
        """
        self._config_resolver = resolver

    async def start(self) -> None:
        """Schedule the background subscriber loop.

        Per the canonical lifecycle pattern
        (``docs/reference/lifecycle-sync.md``), ``self._lifecycle_lock``
        is held across the full body including the success log so
        concurrent ``start()`` / ``stop()`` calls cannot interleave,
        and no lifecycle primitive is published outside the lock
        (those are constructed once in ``__init__``).

        Raises:
            RuntimeError: If the subscriber is unrestartable after a
                previously timed-out ``stop()``.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "PostgresEscalationNotifySubscriber is unrestartable "
                    "after a timed-out stop; construct a fresh subscriber "
                    "instead"
                )
                logger.warning(
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    channel=self._channel,
                    error=msg,
                    note="unrestartable",
                )
                raise RuntimeError(msg)
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="escalation-notify-subscriber",
            )
            # Surface ``MemoryError`` / ``RecursionError`` raised by the
            # ``LISTEN`` loop. Without a done-callback, system-class
            # exceptions stay buffered on the task object and never
            # propagate -- the subscriber would silently die under OOM
            # or stack overflow.
            self._task.add_done_callback(
                log_task_exceptions(
                    logger,
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    channel=self._channel,
                ),
            )
            logger.info(
                CONFLICT_ESCALATION_SUBSCRIBER_STARTED,
                channel=self._channel,
            )

    async def stop(self) -> None:
        """Signal the loop to exit and await its completion.

        Acquires ``self._lifecycle_lock`` so a concurrent ``start()``
        cannot recreate the task mid-stop. Per
        ``docs/reference/lifecycle-sync.md``, lifecycle locks must be
        held across the full body of both ``start`` and ``stop``.

        Raises:
            TimeoutError: If the drain exceeds the stop deadline; the
                subscriber is marked unrestartable.
        """
        async with self._lifecycle_lock:
            self._stop_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()

            # Spawn the await as a separate task and ``shield`` it from
            # the outer ``wait_for`` cancellation: if ``_run`` (or any
            # callee, e.g. a stuck ``subscribe_notifications`` context
            # manager) suppresses ``CancelledError``, ``await task``
            # would block INSIDE the lifecycle lock waiting for the
            # suppressed cancellation to take effect -- the hard
            # deadline would be soft. Same pattern as
            # ``MessageBusBridge.stop()``.
            async def _drain() -> None:
                """Await the cancelled task, swallowing its cancellation."""
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            # Race the drain against a clock-backed deadline so the
            # hard-stop cadence honours the injected ``Clock`` seam
            # (tests inject ``FakeClock`` to drive the timeout on
            # virtual time without spending real wall-clock seconds).
            # ``asyncio.shield(drain_task)`` would tie the deadline to
            # the loop's wall-clock timer via ``asyncio.wait_for``,
            # bypassing the seam.
            deadline_task: asyncio.Task[None] = asyncio.create_task(
                self._clock.sleep(self._stop_drain_timeout_seconds),
            )
            try:
                done, _pending = await asyncio.wait(
                    {drain_task, deadline_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                # Always cancel the deadline task so it does not
                # outlive the stop call. The drain task is cancelled
                # below if the deadline won.
                if not deadline_task.done():
                    deadline_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await deadline_task
            if drain_task in done:
                # Drain completed inside the deadline; surface its
                # exception (if any) through the existing fall-through.
                with contextlib.suppress(asyncio.CancelledError):
                    drain_task.result()
            else:
                # Drain exceeded the hard deadline. Mark the subscriber
                # unrestartable so a future ``start()`` cannot spawn a
                # fresh ``_run`` while the orphan task still holds the
                # LISTEN connection.
                self._stop_failed = True
                # TRY400: ``logger.exception`` here would append a
                # ``TimeoutError`` traceback with no actionable
                # diagnostic information beyond the structured fields.
                logger.error(
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    channel=self._channel,
                    error=(
                        "stop exceeded hard deadline; subscriber marked unrestartable"
                    ),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                # Cancel the drain task so it does not outlive the
                # stop call as an orphan; surface the deadline breach
                # to the caller via ``TimeoutError`` (matches the
                # previous ``asyncio.wait_for`` + ``except TimeoutError``
                # contract that ``stop()``'s callers depend on).
                drain_task.cancel()
                msg = (
                    "PostgresEscalationNotifySubscriber stop exceeded"
                    f" the {self._stop_drain_timeout_seconds}s drain deadline"
                )
                raise TimeoutError(msg)
            self._task = None
            logger.info(CONFLICT_ESCALATION_SUBSCRIBER_STOPPED)
        # Re-create the lifecycle primitives outside the (now
        # released) lock so a subsequent ``start()`` on a different
        # event loop can re-bind them. ``asyncio.Lock`` /
        # ``asyncio.Event`` bind to the running loop on first
        # ``acquire`` / ``set``; the loop they were last bound to
        # may be closed (test pattern: fresh-per-test event loops),
        # so reusing the instances would raise ``RuntimeError: ...
        # is bound to a different event loop``. The recreate runs
        # AFTER the ``async with`` exits so we never swap the lock
        # while still holding it. Production single-loop wiring
        # never hits this path.
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def _resolve_subscriber_enabled(self) -> bool:
        """Resolve the subscriber kill-switch, fail-safe to ``True``.

        Operators flip
        ``communication.escalation_notify_subscriber_enabled=false``
        to pause the cross-instance wake-up subscriber without tearing
        down the lifecycle plumbing. Resolver outage returns ``True``
        because silently pausing the subscriber on a settings hiccup
        would let multi-instance escalations drift toward eventual
        consistency unnecessarily.

        Returns:
            ``True`` when the subscriber is enabled (or on resolver
            outage), ``False`` when an operator has paused it.

        Raises:
            asyncio.CancelledError: Propagated when the resolver call is
                cancelled.
        """
        if self._config_resolver is None:
            return True
        try:
            return await self._config_resolver.get_bool(
                "communication",
                "escalation_notify_subscriber_enabled",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                channel=self._channel,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback_enabled=True,
            )
            return True

    async def _run(self) -> None:
        """Main loop: (re)open a listen connection and dispatch notifies.

        Gated by ``communication.escalation_notify_subscriber_enabled``
        (live, per-iteration): when False the loop stays resident but
        each iteration short-circuits before opening a LISTEN
        connection. The local sweeper + per-resolver timeouts cover
        eventual consistency while the subscriber is paused.

        Raises:
            asyncio.CancelledError: Propagated on shutdown so the loop
                task stops cleanly.
        """
        while not self._stop_event.is_set():
            if await self._resolve_subscriber_enabled():
                try:
                    await self._listen_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                        channel=self._channel,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            else:
                logger.debug(
                    CONFLICT_ESCALATION_SUBSCRIBER_PAUSED,
                    channel=self._channel,
                    reason="paused_by_setting",
                )
            # Reconnect back-off routes through the ``Clock`` seam so
            # ``FakeClock.sleep`` can drive the cadence on virtual
            # time in tests. Race the clock-backed sleep against
            # ``stop_event`` so a stop arriving mid-back-off wakes
            # us immediately instead of waiting out the full delay.
            sleep_task: asyncio.Task[None] = asyncio.create_task(
                self._clock.sleep(self._reconnect_delay),
            )
            stop_wait: asyncio.Task[bool] = asyncio.create_task(
                self._stop_event.wait(),
            )
            try:
                done, _pending = await asyncio.wait(
                    {sleep_task, stop_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (sleep_task, stop_wait):
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
            if stop_wait in done:
                # Stop was set during the back-off; loop will exit
                # via the ``while not self._stop_event.is_set()``
                # guard at the top.
                continue

    async def _listen_once(self) -> None:
        """Open a dedicated connection, LISTEN, and dispatch notifies.

        The actual LISTEN / UNLISTEN / autocommit plumbing lives in
        :meth:`PostgresEscalationRepository.subscribe_notifications`;
        this method just iterates the payload stream and forwards each
        notification to the in-process registry. The dedicated pool
        connection is still held for the subscription lifetime -- pool
        sizing guidance in the class docstring remains accurate.
        """
        async with self._repo.subscribe_notifications(self._channel) as payloads:
            async for payload in payloads:
                if self._stop_event.is_set():
                    break
                await self._dispatch_payload(payload)

    async def _dispatch_payload(self, payload: str) -> None:
        """Interpret a NOTIFY payload and wake the local future."""
        # Payload format: "<escalation_id>:<new_status>" where status is
        # one of decided/expired/cancelled.  ``str.partition`` is
        # infallible, so no try/except around it -- malformed payloads
        # surface as empty ``escalation_id`` / ``status`` below.
        escalation_id, _, status = payload.partition(":")
        if not escalation_id or not status:
            logger.warning(
                CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                note="bad_payload",
                payload=payload,
            )
            return
        try:
            if status == "decided":
                row = await self._repo.get(escalation_id)
                if row is None or row.decision is None:
                    return
                await self._registry.resolve(escalation_id, row.decision)
            elif status in {"expired", "cancelled"}:
                await self._registry.cancel(escalation_id)
            else:
                # Unknown status -- surface so operators catch schema
                # drift (trigger/repo publishing an unrecognised code).
                logger.warning(
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    escalation_id=escalation_id,
                    status=status,
                    note="unknown_notify_status",
                )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                escalation_id=escalation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="notify_dispatch_failed",
            )
