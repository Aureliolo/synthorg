"""Continuous (always-on) simulation mode.

``ContinuousMode`` is an **in-place runner**: ``start()`` executes the
simulation loop on the calling coroutine and only returns once
``stop()`` has been signalled.  This shape differs from the canonical
service lifecycle pattern (``docs/reference/lifecycle-sync.md``)
where ``start()`` spawns a background task and returns immediately.
The canonical pattern's drain timeout / unrestartable flag therefore
does not apply here -- there is no orphan task to drain post-stop.

What carries over from the canonical pattern is the
``self._lifecycle_lock``: it serialises the ``_running`` flag check
and is held only briefly at the top of ``start()`` (acquire, check,
set, release) and again in the ``finally`` to clear the flag. The
lock does NOT span the loop body -- holding it across the run loop
would deadlock a concurrent ``start()`` caller (it would queue on
the lock until the first finished, then enter an empty state).
``stop()`` is synchronous and does not acquire the lock; it merely
sets ``self._stop_event`` so the running ``start()`` coroutine
observes the signal on its next iteration.
"""

import asyncio
from collections import deque

from synthorg.client.config import ContinuousModeConfig
from synthorg.client.models import (
    SimulationConfig,
    SimulationMetrics,
)
from synthorg.client.protocols import (
    ClientInterface,
)
from synthorg.client.runner import SimulationRunner
from synthorg.observability import get_logger
from synthorg.observability.events.client import (
    CONTINUOUS_MODE_DISABLED,
    CONTINUOUS_MODE_STARTED,
    CONTINUOUS_MODE_STOPPED,
)

logger = get_logger(__name__)


class ContinuousMode:
    """Long-running wrapper around :class:`SimulationRunner`.

    Dispatches one simulation run per interval until :meth:`stop`
    is called. Only one run may be active per instance;
    :meth:`start` raises ``RuntimeError`` if already running.
    Use separate instances for concurrent simulation loops.
    """

    def __init__(
        self,
        *,
        config: ContinuousModeConfig,
        runner: SimulationRunner,
    ) -> None:
        """Initialize continuous mode.

        Args:
            config: Continuous-mode configuration (interval,
                concurrency).
            runner: Underlying simulation runner.
        """
        self._config = config
        self._runner = runner
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init
        # Per ``docs/reference/lifecycle-sync.md`` the lifecycle lock
        # is named distinctly from any hot-path lock so a hot-path
        # contention cannot block lifecycle transitions. ContinuousMode
        # has no hot-path lock today, but the rename keeps the
        # codebase uniform across services.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init
        self._runs_completed = 0
        self._running = False
        self._first_run_event_cache: asyncio.Event | None = None

    @property
    def runs_completed(self) -> int:
        """Number of runs completed since the last ``start`` call."""
        return self._runs_completed

    @property
    def first_run_event(self) -> asyncio.Event:
        """Event set after the first run completes since the last ``start``.

        Lazy-constructed on first access so it binds to the running
        event loop rather than the interpreter-startup loop, satisfying
        the ``check_no_loop_bound_init`` guard for restart-on-new-loop
        safety. The check-then-assign is race-free under asyncio because
        neither statement contains an ``await``: between the two lines
        no other coroutine can be scheduled, so two concurrent first
        accesses cannot each construct an Event.
        """
        if self._first_run_event_cache is None:
            self._first_run_event_cache = asyncio.Event()
        return self._first_run_event_cache

    async def start(
        self,
        *,
        sim_config: SimulationConfig,
        clients: tuple[ClientInterface, ...],
    ) -> list[SimulationMetrics]:
        """Run simulations on an interval until ``stop`` is called.

        Args:
            sim_config: Simulation configuration used on every run.
            clients: Clients participating in every run.

        Returns:
            Ordered list of per-run :class:`SimulationMetrics`.

        Raises:
            RuntimeError: When the runner is already running (concurrent
                ``start()`` on the same instance).
        """
        if not self._config.enabled:
            logger.debug(CONTINUOUS_MODE_DISABLED)
            return []
        # Acquire the lifecycle lock briefly to gate the ``_running``
        # transition.  Unlike a service that spawns a background
        # task, ``start()`` runs the loop on the calling coroutine,
        # so the lock does not need to span the loop body -- it only
        # needs to make the "is the runner already busy?" check
        # atomic against concurrent callers.  Holding the lock for
        # the full loop would deadlock a second caller: it would
        # queue on the lock until the first finished, then enter and
        # find ``_running=False``, never observing the conflict.
        async with self._lifecycle_lock:
            if self._running:
                logger.warning(CONTINUOUS_MODE_STARTED, reason="already_running")
                msg = "ContinuousMode is already running"
                raise RuntimeError(msg)
            self._running = True
            self._stop_event.clear()
            self.first_run_event.clear()
            self._runs_completed = 0
        logger.info(
            CONTINUOUS_MODE_STARTED,
            request_interval_sec=self._config.request_interval_sec,
            max_concurrent_requests=self._config.max_concurrent_requests,
        )
        semaphore = asyncio.Semaphore(max(1, self._config.max_concurrent_requests))
        max_history = max(1, self._config.max_concurrent_requests * 100)
        results: deque[SimulationMetrics] = deque(maxlen=max_history)
        try:
            # lint-allow: long-running-loop-kill-switch -- _stop_event gates loop
            while not self._stop_event.is_set():
                async with semaphore:
                    metrics, _ = await self._runner.run(
                        sim_config=sim_config,
                        clients=clients,
                    )
                results.append(metrics)
                self._runs_completed += 1
                self.first_run_event.set()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.request_interval_sec,
                    )
                except TimeoutError:
                    continue
        finally:
            async with self._lifecycle_lock:
                self._running = False
                # Drop the cached event so the next start() rebinds a
                # fresh asyncio.Event to the running loop and a waiter
                # created between this stop and the next start cannot
                # consume the stale "first run completed" signal from
                # this cycle. Re-binding (rather than ``clear()`` on
                # the same instance) is required for cross-loop reuse:
                # an Event whose internal future list was bound to the
                # prior loop cannot be safely awaited on a new one.
                self._first_run_event_cache = None
                # ``_stop_event`` was created at __init__ and the same
                # instance has now seen ``set()`` plus pending-waiter
                # cleanup on this cycle's loop. Replace it with a
                # fresh Event so a cross-loop restart of the same
                # ContinuousMode instance cannot trip a "bound to a
                # different event loop" RuntimeError on the new
                # cycle's first ``wait()``.
                self._stop_event = asyncio.Event()
        return list(results)

    def stop(self) -> None:
        """Signal continuous mode to stop after the current run.

        Synchronous on purpose: only sets the stop event so a caller
        outside the loop can signal teardown without contending with
        the running ``start()`` coroutine. The lifecycle lock is not
        acquired here because the lock guards only the ``_running``
        flag transition, not the long-lived loop body.
        """
        # Setting the stop event when no run is active is a harmless
        # no-op (the next ``start()`` clears it). Only emit the
        # CONTINUOUS_MODE_STOPPED log when an actual run is being
        # interrupted -- otherwise an idle ``stop()`` call would
        # produce a misleading transition record.
        already_stopping = self._stop_event.is_set()
        self._stop_event.set()
        if self._running and not already_stopping:
            logger.info(CONTINUOUS_MODE_STOPPED, runs_completed=self._runs_completed)
