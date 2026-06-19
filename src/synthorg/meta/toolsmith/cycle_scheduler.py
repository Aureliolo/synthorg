"""Autonomous detection driver for the self-extending toolkit.

Runs :meth:`ToolsmithService.run_cycle` on a fixed cadence so the org
detects a recurring capability gap automatically, rather than only when an
operator triggers a cycle by hand. Each tick polls the gap store for
recurring gaps and, for any found, authors + guards a new-tool proposal (the
guard chain still enqueues every proposal for human approval, so the periodic
driver proposes but never auto-applies).

Mirrors the canonical periodic-lifecycle pattern of
:class:`~synthorg.communication.conflict_resolution.escalation.sweeper.EscalationExpirationSweeper`:
loop-bound asyncio primitives are rebound to the running loop atomically by
``_lifecycle_primitives_for_current_loop`` (the EventStreamHub pattern) so the
scheduler survives pytest-asyncio's per-test loops without two racing
``start()`` calls minting different lock objects, the lifecycle lock is held
across the full body of ``start`` / ``stop``, a ``stop()`` drain that exceeds
the hard deadline marks the scheduler unrestartable, and the loop body re-reads
the ``meta.toolsmith_cycle_paused`` kill-switch every tick (fail-safe to
enabled) so an operator can halt self-extension without a restart.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_CYCLE_SCHEDULER_FAILED,
    TOOLSMITH_CYCLE_SCHEDULER_RAN,
    TOOLSMITH_CYCLE_SCHEDULER_STARTED,
    TOOLSMITH_CYCLE_SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_STOP_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0
_MIN_INTERVAL_SECONDS: Final[float] = 60.0
_PAUSE_NS: Final[str] = "meta"
_PAUSE_KEY: Final[str] = "toolsmith_cycle_paused"


class ToolsmithCycleScheduler:
    """Periodic background driver that runs the toolsmith detection cycle."""

    def __init__(
        self,
        service: ToolsmithService,
        *,
        interval_seconds: float,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The toolsmith service whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; must be >= 60 seconds.
            config_resolver: Optional resolver for the
                ``meta.toolsmith_cycle_paused`` kill-switch. When wired,
                every tick re-reads the flag so an operator can pause the
                cycle at runtime; without a resolver the loop runs
                unconditionally (matching the registered default of
                ``False`` / not-paused).

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        if interval_seconds < _MIN_INTERVAL_SECONDS:
            msg = (
                f"interval_seconds must be >= {_MIN_INTERVAL_SECONDS} "
                f"(got {interval_seconds})"
            )
            logger.warning(
                TOOLSMITH_CYCLE_SCHEDULER_FAILED,
                error=msg,
                note="invalid_config",
            )
            raise ValueError(msg)
        self._service = service
        self._interval = interval_seconds
        self._config_resolver = config_resolver
        self._task: asyncio.Task[None] | None = None
        # Loop-bound primitives are rebound to the running loop on first
        # use via ``_lifecycle_primitives_for_current_loop`` so a single
        # scheduler instance can be re-started on a different event loop
        # (pytest-asyncio creates a fresh function-scoped loop per test
        # while a session-scoped app may hold the instance). The rebind
        # check-and-set is synchronous (no await), so two coroutines
        # racing into ``start()`` cannot end up holding different lock
        # objects (the EventStreamHub pattern).
        self._stop_event: asyncio.Event | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._lifecycle_lock_loop: asyncio.AbstractEventLoop | None = None
        self._stop_failed: bool = False

    def _lifecycle_primitives_for_current_loop(
        self,
    ) -> tuple[asyncio.Lock, asyncio.Event]:
        """Return the lifecycle lock + stop event bound to the running loop.

        Rebinds the lifecycle lock AND the stop event together whenever
        the running loop differs from the one they were last bound to,
        dropping any task that belonged to the stale loop. The whole
        check-and-assign runs without an ``await``, so it is atomic under
        asyncio's cooperative scheduling: a second concurrent ``start()``
        observes the already-rebound primitives rather than minting its
        own.

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
            # The previous task (if any) was bound to a now-stale loop.
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
                    "ToolsmithCycleScheduler is unrestartable after a "
                    "timed-out stop; construct a fresh scheduler instead"
                )
                logger.warning(
                    TOOLSMITH_CYCLE_SCHEDULER_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise RuntimeError(msg)
            if self._task is not None and not self._task.done():
                return
            stop_event.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="toolsmith-cycle-scheduler",
            )
            logger.info(
                TOOLSMITH_CYCLE_SCHEDULER_STARTED,
                interval_seconds=self._interval,
            )

    async def stop(self) -> None:
        """Signal the loop to exit and await its completion.

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
                        TOOLSMITH_CYCLE_SCHEDULER_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=_STOP_DRAIN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                self._stop_failed = True
                logger.error(
                    TOOLSMITH_CYCLE_SCHEDULER_FAILED,
                    error=(
                        "stop exceeded hard deadline; scheduler marked unrestartable"
                    ),
                    timeout_seconds=_STOP_DRAIN_TIMEOUT_SECONDS,
                )
                raise
            self._task = None
            self._stop_event = None
            self._lifecycle_lock = None
            logger.info(TOOLSMITH_CYCLE_SCHEDULER_STOPPED)

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
                TOOLSMITH_CYCLE_SCHEDULER_FAILED,
                reason="run_without_stop_event",
                error_type=RuntimeError.__name__,
            )
            raise RuntimeError(msg)
        while not stop_event.is_set():
            if await self._resolve_cycle_enabled():
                try:
                    await self._run_cycle_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        TOOLSMITH_CYCLE_SCHEDULER_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            else:
                logger.debug(
                    TOOLSMITH_CYCLE_SCHEDULER_RAN,
                    proposals=0,
                    note="paused_by_setting",
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the cycle is enabled (inverts the paused flag).

        Reads ``meta.toolsmith_cycle_paused`` from the resolver and inverts
        it. Fail-safe to enabled (returns ``True``) when no resolver is
        wired or the read fails, so a settings-backend outage never silently
        halts self-extension.

        Returns:
            ``True`` when the cycle should run this tick; ``False`` when an
            operator has paused it.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_PAUSE_NS,
            key=_PAUSE_KEY,
            fallback=False,
        )
        return not paused

    async def _run_cycle_once(self) -> None:
        """Run one detection cycle, logging how many proposals it produced.

        ``run_cycle`` catches per-gap authoring errors internally, so this
        only sees a systemic failure (which the caller logs and survives).
        """
        proposals = await self._service.run_cycle()
        logger.info(
            TOOLSMITH_CYCLE_SCHEDULER_RAN,
            proposals=len(proposals),
        )


__all__ = ["ToolsmithCycleScheduler"]
