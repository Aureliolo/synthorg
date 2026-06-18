# module-kind: code
"""Periodic driver for the model-refresh reconcile cycle.

Mirrors :class:`~synthorg.meta.toolsmith.cycle_scheduler.ToolsmithCycleScheduler`:
loop-bound asyncio primitives are rebound to the running loop atomically
(the EventStreamHub pattern) so the scheduler survives pytest-asyncio's
per-test loops; the lifecycle lock is held across ``start`` / ``stop``; a
``stop()`` drain that exceeds the hard deadline marks the scheduler
unrestartable; and every tick re-reads the live ``providers.model_refresh_mode``
discriminator (fail-safe to ``OFF``) so an operator can pause or change
mode without a restart.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
    PROVIDER_MODEL_REFRESH_CYCLE_RAN,
    PROVIDER_MODEL_REFRESH_STARTED,
    PROVIDER_MODEL_REFRESH_STOPPED,
)
from synthorg.providers.management.model_refresh_service import (
    ApplyHook,
    ModelRefreshService,
)
from synthorg.providers.management.refresh_config import (
    RefreshMode,
    resolve_refresh_mode,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_STOP_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0
_MIN_INTERVAL_SECONDS: Final[float] = 60.0
_AUTO_APPLY_NS: Final[str] = "providers"
_AUTO_APPLY_KEY: Final[str] = "model_refresh_auto_apply_within_family"

_SCHEDULED_MODES: Final[frozenset[RefreshMode]] = frozenset(
    {RefreshMode.DETECT_ONLY, RefreshMode.RECONCILE_RECOMMEND},
)


class ModelRefreshScheduler:
    """Periodic background driver that runs the model-refresh cycle."""

    def __init__(
        self,
        service: ModelRefreshService,
        *,
        interval_seconds: float,
        config_resolver: ConfigResolver,
        apply_recommendation: ApplyHook | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The model-refresh service whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; must be >= 60 seconds.
            config_resolver: Resolver for the live mode + auto-apply flag,
                re-read every tick so an operator can retune at runtime.
            apply_recommendation: Optional api-layer hook used when the
                in-family auto-apply flag is set.

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        if interval_seconds < _MIN_INTERVAL_SECONDS:
            msg = (
                f"interval_seconds must be >= {_MIN_INTERVAL_SECONDS} "
                f"(got {interval_seconds})"
            )
            logger.warning(
                PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
                error=msg,
                note="invalid_config",
            )
            raise ValueError(msg)
        self._service = service
        self._interval = interval_seconds
        self._config_resolver = config_resolver
        self._apply_recommendation = apply_recommendation
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._lifecycle_lock_loop: asyncio.AbstractEventLoop | None = None
        self._stop_failed: bool = False

    def _lifecycle_primitives_for_current_loop(
        self,
    ) -> tuple[asyncio.Lock, asyncio.Event]:
        """Return the lifecycle lock + stop event bound to the running loop.

        Returns:
            The ``(lifecycle_lock, stop_event)`` pair bound to the current
            event loop, rebinding atomically when the loop changed.
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
                    "ModelRefreshScheduler is unrestartable after a "
                    "timed-out stop; construct a fresh scheduler instead"
                )
                logger.warning(
                    PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise RuntimeError(msg)
            if self._task is not None and not self._task.done():
                return
            stop_event.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="model-refresh-scheduler",
            )
            logger.info(
                PROVIDER_MODEL_REFRESH_STARTED,
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
                        PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
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
                # ``shield`` keeps ``wait_for`` from cancelling the drain on
                # timeout, so cancel it explicitly rather than leaking an
                # orphaned task onto the (possibly torn-down) loop.
                drain_task.cancel()
                self._stop_failed = True
                logger.error(
                    PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
                    error=(
                        "stop exceeded hard deadline; scheduler marked unrestartable"
                    ),
                    timeout_seconds=_STOP_DRAIN_TIMEOUT_SECONDS,
                )
                raise
            # Keep the lifecycle lock + stop event bound (the
            # ToolsmithCycleScheduler pattern): nulling them while the lock is
            # still held opens a rebind race; ``start()`` clears the event on
            # restart and a loop change rebinds via the loop-identity check.
            self._task = None
            logger.info(PROVIDER_MODEL_REFRESH_STOPPED)

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
        # lint-allow: long-running-loop-kill-switch -- _stop_event drives shutdown.
        while not stop_event.is_set():
            mode = await resolve_refresh_mode(self._config_resolver)
            if mode in _SCHEDULED_MODES:
                await self._run_cycle_once(mode)
            else:
                logger.debug(
                    PROVIDER_MODEL_REFRESH_CYCLE_RAN,
                    note="skipped_by_mode",
                    mode=mode.value,
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def _run_cycle_once(self, mode: RefreshMode) -> None:
        """Run one reconcile cycle under *mode*, surviving systemic failure.

        Raises:
            CancelledError: Propagated so loop cancellation is honoured.
        """
        try:
            auto_apply = await resolve_bool_with_fallback(
                resolver=self._config_resolver,
                namespace=_AUTO_APPLY_NS,
                key=_AUTO_APPLY_KEY,
                fallback=False,
            )
            await self._service.run_cycle(
                mode=mode,
                auto_apply=auto_apply,
                apply_recommendation=self._apply_recommendation,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["ModelRefreshScheduler"]
