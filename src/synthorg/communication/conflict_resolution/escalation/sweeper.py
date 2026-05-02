"""Background task that expires stale escalations.

Runs in the event loop at ``sweeper_interval_seconds`` cadence, calling
:meth:`EscalationQueueStore.mark_expired` so PENDING rows past their
``expires_at`` transition to ``EXPIRED`` without relying on the
resolver coroutine still being alive.

Crucial for restart recovery: after a process restart, any coroutine
that was awaiting a decision has died, but the escalation row remains
``PENDING`` in the store.  The sweeper will eventually reap it.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,  # noqa: TC001
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_EXPIRED,
    CONFLICT_ESCALATION_SWEEPER_FAILED,
    CONFLICT_ESCALATION_SWEEPER_STARTED,
    CONFLICT_ESCALATION_SWEEPER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class EscalationExpirationSweeper:
    """Periodic background task that expires stale escalations."""

    def __init__(
        self,
        store: EscalationQueueStore,
        *,
        interval_seconds: float = 30.0,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialise the sweeper.

        Args:
            store: The queue store whose PENDING rows will be expired.
            interval_seconds: How often to run; must be >= 1 second.
            config_resolver: Optional resolver for the
                ``communication.escalation_sweeper_paused`` flag.  When
                wired, every sweep tick reads the flag so an operator
                can pause the sweeper at runtime without restart;
                without a resolver the loop runs unconditionally
                (matching the registered default of ``False``).
        """
        if interval_seconds < 1.0:
            msg = f"interval_seconds must be >= 1.0 (got {interval_seconds})"
            logger.warning(
                CONFLICT_ESCALATION_SWEEPER_FAILED,
                interval_seconds=interval_seconds,
                error=msg,
                note="invalid_config",
            )
            raise ValueError(msg)
        self._store = store
        self._interval = interval_seconds
        self._config_resolver = config_resolver
        self._task: asyncio.Task[None] | None = None
        # Eager construction of the lifecycle primitives. Python 3.10+
        # ``asyncio.Lock`` / ``asyncio.Event`` are loop-agnostic until
        # first ``acquire()`` / ``set()``, so constructing them at
        # app-wire time (no loop yet) is safe; they bind to whichever
        # loop calls ``start()`` first. Lazy-creating them inside
        # ``start()`` would publish the lock attribute *before* the
        # ``async with`` body ran, letting a racing ``stop()``
        # observe a fresh lock instance and operate on different
        # primitives than the in-flight ``start()``.
        self._stop_event: asyncio.Event = asyncio.Event()
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        # Per ``docs/reference/lifecycle-sync.md``: a ``stop()`` drain
        # that exceeds the hard deadline marks the service unrestartable
        # so a subsequent ``start()`` cannot attach a fresh task while
        # the orphan loop still owns the store. The flag survives any
        # state resets so it remains observable on the next ``start()``
        # call.
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = 30.0

    async def start(self) -> None:
        """Schedule the background loop.

        Idempotent + concurrent-safe: concurrent ``start()`` calls
        serialize on ``self._lifecycle_lock`` so at most one task is
        created even when multiple callers race. Per the canonical
        lifecycle pattern (``docs/reference/lifecycle-sync.md``), the
        lock is held across the full body including the success log
        AND no lifecycle primitive is published outside the lock
        (those are constructed once in ``__init__``).
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "EscalationExpirationSweeper is unrestartable after a "
                    "timed-out stop; construct a fresh sweeper instead"
                )
                logger.warning(
                    CONFLICT_ESCALATION_SWEEPER_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise RuntimeError(msg)
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="escalation-sweeper",
            )
            logger.info(
                CONFLICT_ESCALATION_SWEEPER_STARTED,
                interval_seconds=self._interval,
            )

    async def stop(self) -> None:
        """Signal the loop to exit and await its completion.

        Acquires ``self._lifecycle_lock`` so a concurrent ``start()``
        cannot recreate the task mid-stop. Per
        ``docs/reference/lifecycle-sync.md``, lifecycle locks must be
        held across the full body of both ``start`` and ``stop``.

        Idle stop: when nothing was ever started (``_task`` is None
        and ``_stop_event`` was never set) ``stop()`` still acquires
        the lock and returns cleanly; this keeps the lifecycle
        contract uniform regardless of whether ``start()`` ever ran.
        """
        async with self._lifecycle_lock:
            self._stop_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()

            # Spawn the await as a separate task and ``shield`` it from
            # the outer ``wait_for`` cancellation: if ``_run`` (or any
            # callee) suppresses ``CancelledError``, ``await task``
            # would block INSIDE the lifecycle lock waiting for the
            # suppressed cancellation to take effect -- the hard
            # deadline would be soft. With ``shield``, the outer
            # ``wait_for`` times out the wait only; the shielded await
            # keeps running in the background but does not prevent
            # ``stop()`` from exiting and releasing
            # ``self._lifecycle_lock``. Same pattern as
            # ``MessageBusBridge.stop()``.
            async def _drain() -> None:
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected: we just cancelled the task.
                    pass
                except MemoryError, RecursionError:
                    # Catastrophic interpreter-level errors must
                    # surface to the caller; never log-and-swallow
                    # because that hides loss-of-process conditions
                    # behind a "clean shutdown" log line.
                    raise
                except Exception as exc:
                    # Best-effort shutdown: never propagate, but elevate
                    # to WARNING so real failures surface in production
                    # logs instead of being lost at DEBUG.
                    logger.warning(
                        CONFLICT_ESCALATION_SWEEPER_FAILED,
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
                # Drain exceeded the hard deadline. Mark the sweeper
                # unrestartable so a future ``start()`` cannot spawn a
                # fresh ``_run`` while the orphan task still owns the
                # store. Leave ``_task`` + ``_stop_event`` intact so
                # subsequent inspection reflects the incomplete shutdown.
                self._stop_failed = True
                # TRY400: ``logger.exception`` here would append a
                # ``TimeoutError`` traceback with no actionable
                # diagnostic information beyond the structured fields.
                logger.error(  # noqa: TRY400
                    CONFLICT_ESCALATION_SWEEPER_FAILED,
                    error=("stop exceeded hard deadline; sweeper marked unrestartable"),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                raise
            self._task = None
            # Re-create the loop-bound stop event WHILE holding the
            # lifecycle lock. Doing it outside the lock would leave a
            # window where a racing ``start()`` could spawn ``_run()``
            # bound to the OLD event before this assignment lands; a
            # later stop() would then signal a different event than
            # the running task is waiting on, stalling shutdown until
            # the interval timeout. ``asyncio.Event`` binds to the
            # running loop on first ``set()``, so a fresh instance is
            # always required across loops; ``self._lifecycle_lock``
            # itself MUST stay the same instance for the service's
            # lifetime. Tests that span multiple event loops construct
            # a fresh sweeper per loop instead of reusing one.
            self._stop_event = asyncio.Event()
            logger.info(CONFLICT_ESCALATION_SWEEPER_STOPPED)

    async def _run(self) -> None:
        """Main loop body."""
        while not self._stop_event.is_set():
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except MemoryError, RecursionError:
                # Match the ``_drain`` shape: surface catastrophic
                # interpreter-level errors instead of looping past
                # them at WARNING.
                raise
            except Exception as exc:
                logger.warning(
                    CONFLICT_ESCALATION_SWEEPER_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval,
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                # Explicit re-raise: external cancellation (stop() or
                # loop shutdown) must terminate the loop, not fall
                # through to another sweep iteration.
                raise

    async def _sweep_once(self) -> None:
        """Expire any rows whose deadline has passed.

        Emits the same event constant on every iteration (at ``info``
        when rows were expired, at ``debug`` when the pass was clean)
        so operators can detect a silent sweeper (store returns 0 due
        to a timezone / WHERE-clause bug) by the absence of debug logs.

        Honors the ``communication.escalation_sweeper_paused`` flag at
        the start of each tick: when ``True`` the loop stays resident
        but the call short-circuits so an operator can suspend
        expiration during incident investigation without tearing down
        the lifecycle plumbing.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="communication",
            key="escalation_sweeper_paused",
            fallback=False,
        )
        if paused:
            logger.debug(
                CONFLICT_ESCALATION_EXPIRED,
                expired_count=0,
                note="sweeper_paused_by_setting",
            )
            return
        now = datetime.now(UTC)
        expired = await self._store.mark_expired(now.isoformat())
        if expired:
            logger.info(
                CONFLICT_ESCALATION_EXPIRED,
                expired_count=len(expired),
                expired_ids=list(expired),
            )
        else:
            logger.debug(CONFLICT_ESCALATION_EXPIRED, expired_count=0)
