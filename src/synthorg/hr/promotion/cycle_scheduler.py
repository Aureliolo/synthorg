"""Periodic driver for the automatic promotion cycle.

Runs :meth:`PromotionService.run_cycle` on a fixed cadence so the org
re-evaluates agent seniority automatically rather than only when an
operator triggers a cycle by hand. Each tick scans active agents,
applies the changes the approval strategy auto-approves, and enqueues
human-gated changes as approval items (so the driver proposes but never
bypasses a human gate).

Mirrors the canonical periodic-lifecycle pattern of
:class:`~synthorg.meta.toolsmith.cycle_scheduler.ToolsmithCycleScheduler`:
loop-bound asyncio primitives are rebound to the running loop atomically
so the scheduler survives pytest-asyncio's per-test loops, the lifecycle
lock is held across ``start`` / ``stop``, a ``stop()`` drain that exceeds
the hard deadline marks the scheduler unrestartable, and the loop body
re-reads the ``hr.promotion_cycle_paused`` kill-switch every tick
(fail-safe to enabled) so an operator can halt promotions without a
restart.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.promotion.service import PromotionService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.promotion import (
    PROMOTION_CYCLE_RAN,
    PROMOTION_CYCLE_SCHEDULER_FAILED,
    PROMOTION_CYCLE_SCHEDULER_STARTED,
    PROMOTION_CYCLE_SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_STOP_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0
_MIN_INTERVAL_SECONDS: Final[float] = 60.0
_PAUSE_NS: Final[str] = "hr"
_PAUSE_KEY: Final[str] = "promotion_cycle_paused"


class PromotionCycleScheduler:
    """Periodic background driver that runs the promotion cycle."""

    def __init__(
        self,
        service: PromotionService,
        *,
        interval_seconds: float,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The promotion service whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; floored at 60s.
            config_resolver: Optional resolver for the
                ``hr.promotion_cycle_paused`` kill-switch. When wired,
                every tick re-reads the flag so an operator can pause the
                cycle at runtime; without a resolver the loop runs
                unconditionally (matching the registered default of
                ``False`` / not-paused).
        """
        self._service = service
        self._interval = max(interval_seconds, _MIN_INTERVAL_SECONDS)
        self._config_resolver = config_resolver
        self._task: asyncio.Task[None] | None = None
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
                    "PromotionCycleScheduler is unrestartable after a "
                    "timed-out stop; construct a fresh scheduler instead"
                )
                logger.warning(
                    PROMOTION_CYCLE_SCHEDULER_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise RuntimeError(msg)
            if self._task is not None and not self._task.done():
                return
            stop_event.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="promotion-cycle-scheduler",
            )
            logger.info(
                PROMOTION_CYCLE_SCHEDULER_STARTED,
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
                        PROMOTION_CYCLE_SCHEDULER_FAILED,
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
                # The shield kept wait_for's cancellation off drain_task;
                # cancel it explicitly so it cannot outlive stop() as an
                # orphaned task that logs into a torn-down loop.
                drain_task.cancel()
                self._stop_failed = True
                logger.error(
                    PROMOTION_CYCLE_SCHEDULER_FAILED,
                    error=(
                        "stop exceeded hard deadline; scheduler marked unrestartable"
                    ),
                    timeout_seconds=_STOP_DRAIN_TIMEOUT_SECONDS,
                )
                raise
            self._task = None
            self._stop_event = None
            self._lifecycle_lock = None
            logger.info(PROMOTION_CYCLE_SCHEDULER_STOPPED)

    async def _run(self) -> None:
        """Main loop: run a cycle, then wait the interval (or until stop).

        Raises:
            RuntimeError: If invoked before ``start()`` set the stop event.
            asyncio.CancelledError: Propagated on shutdown so the loop ends.
        """
        stop_event = self._stop_event
        if stop_event is None:  # defensive; start() guarantees non-None
            msg = "_run invoked without an initialised stop event"
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
                        PROMOTION_CYCLE_SCHEDULER_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            else:
                logger.debug(
                    PROMOTION_CYCLE_RAN,
                    applied=0,
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

        Reads ``hr.promotion_cycle_paused`` from the resolver and inverts
        it. Fail-safe to enabled (returns ``True``) when no resolver is
        wired or the read fails, so a settings-backend outage never
        silently halts promotions.

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
        """Run one promotion cycle, logging how many changes it applied."""
        applied = await self._service.run_cycle()
        logger.info(
            PROMOTION_CYCLE_RAN,
            applied=len(applied),
        )


__all__ = ["PromotionCycleScheduler"]
