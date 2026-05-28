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
from typing import TYPE_CHECKING, Final

from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_EXPIRED,
    CONFLICT_ESCALATION_SWEEPER_FAILED,
    CONFLICT_ESCALATION_SWEEPER_STARTED,
    CONFLICT_ESCALATION_SWEEPER_STOPPED,
)
from synthorg.observability.metrics_hub import record_escalation_outcome
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)
_DEFAULT_INTERVAL_SECONDS: Final[float] = 30.0


class EscalationExpirationSweeper:
    """Periodic background task that expires stale escalations."""

    def __init__(
        self,
        store: EscalationQueueStore,
        *,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
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
        # Loop-bound asyncio primitives are deferred until ``start()``
        # so the sweeper can be safely re-started on a different event
        # loop.  Eager construction in ``__init__`` would stick them to
        # whichever loop happened to be current at instantiation -- a
        # problem in tests where pytest-asyncio creates a fresh
        # function-scoped loop per test while the sweeper instance is
        # held by a session-scoped Litestar app.  ``_lifecycle_lock``
        # still serialises concurrent start/stop on the SAME loop;
        # the running-loop check inside ``start()`` handles the
        # cross-loop case before we ever try to acquire it.
        self._stop_event: asyncio.Event | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        # Per ``docs/reference/lifecycle-sync.md``: a ``stop()`` drain
        # that exceeds the hard deadline marks the service unrestartable
        # so a subsequent ``start()`` cannot attach a fresh task while
        # the orphan loop still owns the store. The flag survives any
        # state resets so it remains observable on the next ``start()``
        # call.
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = 30.0

    def _task_is_on_current_loop(self) -> bool:
        """True iff the existing task is alive on the running loop.

        Used internally by ``start()`` to detect cross-loop reuse.
        Returns ``True`` when the task or loop cannot be introspected
        (e.g. ``MagicMock(spec=asyncio.Task)`` in tests where
        ``get_loop()`` returns a mock instead of a real loop) so that
        unit tests that mock ``asyncio.create_task`` are not penalised
        by spurious task drops.
        """
        if self._task is None or self._task.done():
            return False
        try:
            # ``object`` annotation defeats mypy's narrowing of
            # ``Task.get_loop`` so the runtime ``isinstance`` check
            # below is reachable for ``MagicMock(spec=Task)`` test
            # fixtures whose ``get_loop`` returns a mock value.
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
        """Discard task/primitives bound to a closed-or-other event loop."""
        self._task = None
        self._stop_event = None
        self._lifecycle_lock = None

    async def start(self) -> None:
        """Schedule the background loop.

        Idempotent + concurrent-safe: concurrent ``start()`` calls
        serialize on ``self._lifecycle_lock`` so at most one task is
        created even when multiple callers race. Per the canonical
        lifecycle pattern (``docs/reference/lifecycle-sync.md``), the
        lock is held across the full body including the success log
        AND no lifecycle primitive is published outside the lock.

        When a previous ``start()`` ran on a different (now-closed)
        loop -- e.g. across pytest-asyncio's per-test loops -- the
        stale task and its loop-bound primitives are discarded and
        fresh ones are spawned on the current loop.
        """
        # Detect cross-loop reuse before touching any lifecycle primitive.
        # Otherwise ``async with self._lifecycle_lock`` would itself raise
        # ``<Lock> is bound to a different event loop`` on the FIRST line
        # of the function and there is nothing the sweeper can do to
        # recover after that.
        if self._task is not None and not self._task_is_on_current_loop():
            self._drop_stale_loop_state()
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
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
        if self._lifecycle_lock is None or self._stop_event is None:
            # Sweeper was never started; nothing to stop.
            return
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
                except Exception as exc:
                    reraise_critical(exc)
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
                logger.error(
                    CONFLICT_ESCALATION_SWEEPER_FAILED,
                    error=("stop exceeded hard deadline; sweeper marked unrestartable"),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                raise
            self._task = None
            # Drop loop-bound primitives so the next ``start()`` rebinds
            # them to whichever loop is current then.  Required for
            # tests that span multiple event loops (pytest-asyncio's
            # function-scoped loops) to keep a single session-scoped
            # sweeper instance from carrying loop A's primitives into
            # loop B.
            self._stop_event = None
            self._lifecycle_lock = None
            logger.info(CONFLICT_ESCALATION_SWEEPER_STOPPED)

    async def _resolve_sweeper_enabled(self) -> bool:
        """Return True when the sweeper is enabled (inverts the paused flag).

        Reads ``communication.escalation_sweeper_paused`` from the
        resolver and inverts it so the kill-switch shape matches the
        ``_resolve_*_enabled`` convention.  Fail-safes to enabled
        (returns ``True``) when the resolver is unavailable or the
        read fails -- a settings-backend outage must not silently
        flip the sweeper off because stale escalations would then
        accumulate forever.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="communication",
            key="escalation_sweeper_paused",
            fallback=False,
        )
        return not paused

    async def _run(self) -> None:
        """Main loop body.

        Captures ``self._stop_event`` into a local so a concurrent
        ``stop()`` that sets it to ``None`` cannot trip an ``is_set``
        attribute access here.  ``start()`` guarantees the event is
        non-None at the moment the task is spawned.

        Gated by ``communication.escalation_sweeper_paused`` (live,
        per-tick, inverted): when the sweeper is paused every tick
        short-circuits the work but the loop stays resident so
        operators can resume without restarting the lifecycle plumbing.
        """
        stop_event = self._stop_event
        if stop_event is None:  # defensive; start() guarantees non-None
            msg = "_run invoked without an initialised stop event"
            raise RuntimeError(msg)
        while not stop_event.is_set():
            if await self._resolve_sweeper_enabled():
                try:
                    await self._sweep_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    reraise_critical(exc)
                    logger.warning(
                        CONFLICT_ESCALATION_SWEEPER_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    record_escalation_outcome(outcome="sweeper_failed")
            else:
                logger.debug(
                    CONFLICT_ESCALATION_EXPIRED,
                    expired_count=0,
                    note="sweeper_paused_by_setting",
                )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
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

        The ``communication.escalation_sweeper_paused`` kill-switch is
        evaluated by ``_run`` before this method is invoked, so a
        paused sweeper short-circuits at the loop body rather than
        partway through this helper.
        """
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
