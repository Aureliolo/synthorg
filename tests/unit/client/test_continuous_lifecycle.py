"""Lifecycle tests for ``ContinuousMode``.

ContinuousMode is an in-place runner (``start()`` executes the loop
on the calling coroutine until ``stop()`` is signalled). The
``_lifecycle_lock`` serialises only the ``_running`` flag check at
the top of ``start()`` (acquire / check / set / release) and again
in the ``finally`` to clear the flag; it is NOT held across the run
loop body, so a second ``start()`` raises ``RuntimeError`` rather
than queuing behind the first. ``stop()`` is synchronous and does
not acquire the lock; it sets ``self._stop_event`` so the running
``start()`` coroutine observes the signal on its next iteration.
"""

import asyncio
from collections.abc import Iterator

import pytest
from typeguard import suppress_type_checks

from synthorg.client.config import ContinuousModeConfig
from synthorg.client.continuous import ContinuousMode
from synthorg.client.models import SimulationConfig, SimulationMetrics

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_fake_runner() -> Iterator[None]:
    """Suppress typeguard: tests drive an intentional ``_FakeRunner`` double.

    The fake records ``run()`` invocations and does not satisfy the concrete
    runner type ``ContinuousMode`` is annotated against; these tests verify
    lifecycle behaviour, not runner type conformance.
    """
    with suppress_type_checks():
        yield


class _FakeRunner:
    """Records ``run()`` invocations and signals readiness via ``Event``.

    The ``ready`` event lets a test wait for the runner's first entry
    deterministically instead of relying on ``asyncio.sleep(0)`` cycles
    to let the inner loop schedule itself. Tests that need to drive
    the second ``start()`` only after the first has actually entered
    the runner await ``runner.ready.wait()``.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.ready = asyncio.Event()

    async def run(
        self,
        *,
        sim_config: SimulationConfig,
        clients: tuple[object, ...],
    ) -> tuple[SimulationMetrics, list[object]]:
        del sim_config, clients
        self.calls += 1
        self.ready.set()
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
        # ``runner.ready.wait()`` only proves ``run()`` has entered;
        # it does NOT prove the lifecycle lock is still held (the
        # current ``ContinuousMode`` releases the lock before the
        # run loop body). What it gives us is sequencing: the first
        # ``start()`` has already passed the ``_running`` check and
        # set the flag, so the second call below sees ``_running ==
        # True`` and is forced down the "already running" branch.
        await runner.ready.wait()

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
        # Wait for the first run to enter (the runner sets ``ready``
        # on every entry); racing the wall-clock with a fixed sleep
        # is what makes lifecycle tests flaky on busy CI runners.
        await runner.ready.wait()
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


class TestContinuousModeFirstRunEvent:
    """``first_run_event`` is the deterministic sync seam for callers
    that need to observe the first-run boundary without polling
    ``runs_completed``.
    """

    async def test_property_returns_event_and_is_cached(self) -> None:
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(enabled=True),
            runner=runner,  # type: ignore[arg-type]
        )
        first = mode.first_run_event
        second = mode.first_run_event
        assert isinstance(first, asyncio.Event)
        assert first is second
        assert not first.is_set()

    async def test_event_set_after_first_run(self) -> None:
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
        await mode.first_run_event.wait()
        assert mode.runs_completed >= 1
        mode.stop()
        await task

    async def test_lifecycle_events_rebind_after_run(self) -> None:
        """Both lifecycle events must be replaced on cycle exit.

        ``_first_run_event_cache`` and ``_stop_event`` are bound to
        the cycle's running loop; reusing the same instances on a
        future loop can raise "bound to a different event loop"
        RuntimeError on a fresh ``wait()``. The finally block must
        drop / replace them so the next ``start()`` re-binds.
        """
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(
                enabled=True,
                request_interval_sec=0.001,
                max_concurrent_requests=1,
            ),
            runner=runner,  # type: ignore[arg-type]
        )

        original_stop_event = mode._stop_event
        cycle = asyncio.create_task(
            mode.start(sim_config=_sim_config(), clients=()),
        )
        await mode.first_run_event.wait()
        mode.stop()
        await cycle

        assert mode._first_run_event_cache is None
        assert mode._stop_event is not original_stop_event
        assert isinstance(mode._stop_event, asyncio.Event)
        assert not mode._stop_event.is_set()

    async def test_event_cleared_between_cycles(self) -> None:
        """A waiter created between cycles must not see a stale signal.

        Without clearing the cached event when ``start()`` exits, a
        post-stop ``first_run_event.wait()`` would observe the prior
        cycle's set state and resolve immediately, even though no run
        has completed since the new cycle began.
        """
        runner = _FakeRunner()
        mode = ContinuousMode(
            config=ContinuousModeConfig(
                enabled=True,
                request_interval_sec=0.001,
                max_concurrent_requests=1,
            ),
            runner=runner,  # type: ignore[arg-type]
        )

        first_cycle = asyncio.create_task(
            mode.start(sim_config=_sim_config(), clients=()),
        )
        await mode.first_run_event.wait()
        mode.stop()
        await first_cycle

        assert not mode.first_run_event.is_set()
