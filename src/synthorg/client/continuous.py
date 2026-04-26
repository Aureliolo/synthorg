"""Continuous (always-on) simulation mode."""

import asyncio
from collections import deque

from synthorg.client.config import ContinuousModeConfig  # noqa: TC001
from synthorg.client.models import (
    SimulationConfig,  # noqa: TC001
    SimulationMetrics,  # noqa: TC001
)
from synthorg.client.protocols import (
    ClientInterface,  # noqa: TC001
)
from synthorg.client.runner import SimulationRunner  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.client import CONTINUOUS_MODE_DISABLED

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
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._runs_completed = 0
        self._running = False

    @property
    def runs_completed(self) -> int:
        """Number of runs completed since the last ``start`` call."""
        return self._runs_completed

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
        """
        if not self._config.enabled:
            logger.debug(CONTINUOUS_MODE_DISABLED)
            return []
        async with self._lock:
            if self._running:
                msg = "ContinuousMode is already running"
                raise RuntimeError(msg)
            self._running = True
            self._stop_event.clear()
            self._runs_completed = 0
        semaphore = asyncio.Semaphore(max(1, self._config.max_concurrent_requests))
        max_history = max(1, self._config.max_concurrent_requests * 100)
        results: deque[SimulationMetrics] = deque(maxlen=max_history)
        try:
            while not self._stop_event.is_set():
                async with semaphore:
                    metrics, _ = await self._runner.run(
                        sim_config=sim_config,
                        clients=clients,
                    )
                results.append(metrics)
                self._runs_completed += 1
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.request_interval_sec,
                    )
                except TimeoutError:
                    continue
        finally:
            async with self._lock:
                self._running = False
        return list(results)

    def stop(self) -> None:
        """Signal continuous mode to stop after the current run."""
        self._stop_event.set()
