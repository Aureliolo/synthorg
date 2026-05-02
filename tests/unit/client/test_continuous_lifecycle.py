"""Lifecycle tests for ``ContinuousMode``.

ContinuousMode is an in-place runner (``start()`` executes the loop
synchronously on the caller until ``stop()`` is signalled). The
``_lifecycle_lock`` serialises concurrent ``start()`` calls so the
"already running" RuntimeError is raised reliably, and the lock
spans the full body so a racing caller cannot enter mid-loop.
"""

import asyncio
from typing import Any

import pytest

from synthorg.client.config import ContinuousModeConfig
from synthorg.client.continuous import ContinuousMode
from synthorg.client.models import SimulationConfig, SimulationMetrics

pytestmark = pytest.mark.unit


class _FakeRunner:
    """Records run() invocations and returns canned metrics."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        *,
        sim_config: SimulationConfig,
        clients: tuple[Any, ...],
    ) -> tuple[SimulationMetrics, list[Any]]:
        del sim_config, clients
        self.calls += 1
        await asyncio.sleep(0)
        return (
            SimulationMetrics(
                total_requirements=1,
                total_tasks_created=1,
                tasks_accepted=1,
            ),
            [],
        )


def _sim_config() -> SimulationConfig:
    return SimulationConfig(
        project_id="proj-1",
        clients_per_round=1,
        requirements_per_client=1,
    )


class TestContinuousModeLifecycleLock:
    """The lifecycle lock prevents concurrent start() from running twice."""

    async def test_double_start_raises_when_already_running(self) -> None:
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(
                enabled=True,
                request_interval_sec=10.0,
                max_concurrent_requests=1,
            ),
            runner=runner,  # type: ignore[arg-type]
        )

        first = asyncio.create_task(
            mode.start(sim_config=_sim_config(), clients=()),
        )
        # Yield so the first task acquires the lock and starts the
        # loop; without the yield, the second start() may run first.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="already running"):
            await mode.start(sim_config=_sim_config(), clients=())

        mode.stop()
        await first

    async def test_disabled_short_circuits_without_acquiring_lock(self) -> None:
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(enabled=False),
            runner=runner,  # type: ignore[arg-type]
        )
        results = await mode.start(sim_config=_sim_config(), clients=())
        assert results == []
        assert runner.calls == 0

    async def test_stop_releases_runner_loop(self) -> None:
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(
                enabled=True,
                request_interval_sec=0.001,
                max_concurrent_requests=1,
            ),
            runner=runner,  # type: ignore[arg-type]
        )

        task = asyncio.create_task(
            mode.start(sim_config=_sim_config(), clients=()),
        )
        # Let the loop run once.
        await asyncio.sleep(0.01)
        mode.stop()
        results = await task
        assert len(results) >= 1
        assert mode.runs_completed >= 1

    async def test_lifecycle_lock_attribute_present(self) -> None:
        """Smoke test: the canonical lock name is in place."""
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(enabled=True),
            runner=runner,  # type: ignore[arg-type]
        )
        assert isinstance(mode._lifecycle_lock, asyncio.Lock)
