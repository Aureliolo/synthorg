# module-kind: code
"""Shared lifecycle scaffolding for periodic background cycle schedulers.

Several independently-owned subsystems run a service method on a fixed
cadence: the toolsmith detection cycle, the HR promotion cycle, and the
provider model-refresh reconcile cycle. Each had grown a byte-identical
copy of the same delicate asyncio lifecycle: loop-bound primitives rebound
to the running loop atomically (the EventStreamHub pattern) so the
scheduler survives pytest-asyncio's per-test loops, a lifecycle lock held
across ``start`` / ``stop``, a bounded ``stop()`` drain that marks the
scheduler unrestartable on timeout, and a per-tick kill-switch read.

:class:`AsyncCycleScheduler` owns that machinery once. Subclasses supply
the domain work and (optionally) the per-tick enabled check:

* :meth:`_run_cycle_once` -- one unit of domain work (required).
* :meth:`_resolve_cycle_enabled` -- per-tick kill-switch read; defaults to
  always-enabled so a mode-discriminated subclass can read its own
  discriminator inside :meth:`_run_cycle_once` instead.
* :meth:`_log_cycle_paused` -- emit a skipped-tick log when disabled;
  defaults to a no-op.

The event-name constants (started / stopped / failed) and the task name
are passed in so each subsystem keeps its own telemetry vocabulary.
``reset_primitives_on_stop=False`` keeps the lifecycle lock + stop event
bound after ``stop()`` (the deliberate no-null rebind-race guard the
model-refresh scheduler relies on).
"""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.observability import get_logger, safe_error_description

logger = get_logger(__name__)

MIN_INTERVAL_SECONDS: Final[float] = 60.0


class AsyncCycleScheduler(ABC):
    """Loop-bound periodic driver base for background cycle schedulers.

    Owns the lifecycle (primitives, ``start``, ``stop``, ``_run``); a
    subclass implements :meth:`_run_cycle_once` and may override
    :meth:`_resolve_cycle_enabled` / :meth:`_log_cycle_paused`.
    """

    def __init__(  # noqa: PLR0913 -- event vocabulary + lifecycle policy knobs
        self,
        *,
        interval_seconds: float,
        task_name: str,
        started_event: str,
        stopped_event: str,
        failed_event: str,
        reset_primitives_on_stop: bool = True,
        drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        """Initialise the scheduler skeleton.

        Args:
            interval_seconds: Cadence between cycles; must be at least
                :data:`MIN_INTERVAL_SECONDS`.
            task_name: ``asyncio.Task`` name for the background loop.
            started_event: Structured event logged on ``start``.
            stopped_event: Structured event logged on a clean ``stop``.
            failed_event: Structured event logged on a cycle/lifecycle
                failure (also reused for the invalid-config rejection).
            reset_primitives_on_stop: When ``True`` (default), a clean
                ``stop`` nulls the lifecycle lock + stop event; when
                ``False`` they stay bound (rebind-race guard).
            drain_timeout_seconds: Hard deadline for the ``stop`` drain
                before the scheduler is marked unrestartable.

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum or
                ``drain_timeout_seconds`` is not positive.
        """
        if interval_seconds < MIN_INTERVAL_SECONDS:
            msg = (
                f"interval_seconds must be >= {MIN_INTERVAL_SECONDS} "
                f"(got {interval_seconds})"
            )
            logger.warning(failed_event, error=msg, note="invalid_config")
            raise ValueError(msg)
        if drain_timeout_seconds <= 0:
            msg = (
                f"drain_timeout_seconds must be positive (got {drain_timeout_seconds})"
            )
            logger.warning(failed_event, error=msg, note="invalid_config")
            raise ValueError(msg)
        self._interval = interval_seconds
        self._task_name = task_name
        self._started_event = started_event
        self._stopped_event = stopped_event
        self._failed_event = failed_event
        self._reset_primitives_on_stop = reset_primitives_on_stop
        self._drain_timeout = drain_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        # Loop-bound primitives are rebound to the running loop on first
        # use so a single instance can be re-started on a different event
        # loop (pytest-asyncio creates a fresh function-scoped loop per
        # test while a session-scoped app may hold the instance). The
        # rebind check-and-set is synchronous, so two coroutines racing
        # into ``start()`` cannot end up holding different lock objects.
        self._stop_event: asyncio.Event | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._lifecycle_lock_loop: asyncio.AbstractEventLoop | None = None
        self._stop_failed: bool = False

    def _lifecycle_primitives_for_current_loop(
        self,
    ) -> tuple[asyncio.Lock, asyncio.Event]:
        """Return the lifecycle lock + stop event bound to the running loop.

        Rebinds both together whenever the running loop differs from the
        one they were last bound to, dropping any task that belonged to
        the stale loop. The whole check-and-assign runs without an
        ``await``, so it is atomic under asyncio's cooperative scheduling.

        Returns:
            The ``(lifecycle_lock, stop_event)`` pair bound to the current
            event loop.
        """
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if (
            self._lifecycle_lock is None
            or self._stop_event is None
            or (current is not None and self._lifecycle_lock_loop is not current)
        ):
            self._lifecycle_lock = asyncio.Lock()
            self._stop_event = asyncio.Event()
            self._lifecycle_lock_loop = current
            self._task = None
        return self._lifecycle_lock, self._stop_event

    async def start(self) -> None:
        """Schedule the background cycle loop (idempotent, concurrent-safe).

        Raises:
            RuntimeError: If the scheduler is unrestartable after a
                previously timed-out ``stop()``.
        """
        lifecycle_lock, stop_event = self._lifecycle_primitives_for_current_loop()
        async with lifecycle_lock:
            if self._stop_failed:
                msg = (
                    f"{type(self).__name__} is unrestartable after a "
                    "timed-out stop; construct a fresh scheduler instead"
                )
                logger.warning(self._failed_event, error=msg, note="unrestartable")
                raise RuntimeError(msg)
            if self._task is not None and not self._task.done():
                return
            stop_event.clear()
            self._task = asyncio.create_task(self._run(), name=self._task_name)
            logger.info(self._started_event, interval_seconds=self._interval)

    async def stop(self) -> None:
        """Signal the loop to exit and await its completion.

        No-ops when the scheduler was never started or its background task
        is already gone.

        Raises:
            TimeoutError: If the drain exceeds the stop deadline; the
                scheduler is then marked unrestartable.
        """
        if self._lifecycle_lock is None or self._stop_event is None:
            return
        async with self._lifecycle_lock:
            self._stop_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()

            async def _drain() -> None:
                """Await the cancelled task, swallowing its cancellation."""
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        self._failed_event,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=self._drain_timeout,
                )
            except TimeoutError:
                # The shield kept wait_for's cancellation off drain_task;
                # cancel it AND await its completion so it cannot outlive
                # stop() as an orphaned task that logs into a torn-down loop.
                drain_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain_task
                self._stop_failed = True
                logger.error(
                    self._failed_event,
                    error=(
                        "stop exceeded hard deadline; scheduler marked unrestartable"
                    ),
                    timeout_seconds=self._drain_timeout,
                )
                raise
            # The default (``reset_primitives_on_stop=True``) nulls the
            # lifecycle lock + stop event here; ``start()`` then rebinds
            # fresh primitives on restart (clearing the event, rebinding on
            # a loop change via the loop-identity check). A subclass that
            # restarts on the SAME loop without the rebind passes
            # ``reset_primitives_on_stop=False`` to keep them bound.
            self._task = None
            if self._reset_primitives_on_stop:
                self._stop_event = None
                self._lifecycle_lock = None
            logger.info(self._stopped_event)

    async def _run(self) -> None:
        """Main loop: run a cycle, then wait the interval (or until stop).

        Raises:
            RuntimeError: If invoked before ``start()`` set the stop event.
            asyncio.CancelledError: Propagated on shutdown so the loop ends.
        """
        stop_event = self._stop_event
        if stop_event is None:  # defensive; start() guarantees non-None
            msg = "_run invoked without an initialised stop event"
            logger.error(
                self._failed_event,
                reason="run_without_stop_event",
                error_type=RuntimeError.__name__,
                error=msg,
            )
            raise RuntimeError(msg)
        # lint-allow: long-running-loop-kill-switch -- stop_event + enable re-read
        while not stop_event.is_set():
            try:
                enabled = await self._resolve_cycle_enabled()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # A broken kill-switch read must not silently kill the loop;
                # log it and fail safe to enabled so the cycle keeps running.
                reraise_critical(exc)
                logger.warning(
                    self._failed_event,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="cycle_enabled_read_failed",
                )
                enabled = True
            if enabled:
                try:
                    await self._run_cycle_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        self._failed_event,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            else:
                self._log_cycle_paused()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the cycle should run this tick.

        Default always-enabled. Bool-paused subclasses override to read a
        kill-switch; mode-discriminated subclasses leave this and branch on
        the discriminator inside :meth:`_run_cycle_once`.

        Returns:
            ``True`` when :meth:`_run_cycle_once` should run this tick.
        """
        return True

    @abstractmethod
    async def _run_cycle_once(self) -> None:
        """Run one unit of domain work (abstract; a subclass must override)."""
        raise NotImplementedError

    def _log_cycle_paused(self) -> None:  # noqa: B027 -- intentional default no-op hook, not abstract
        """Emit a skipped-tick log when the cycle is disabled (default no-op)."""


__all__ = ["MIN_INTERVAL_SECONDS", "AsyncCycleScheduler"]
