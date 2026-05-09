"""Integration tests for ContinuousMode."""

import asyncio

import pytest

from synthorg.client import (
    AIClient,
    ClientProfile,
    ContinuousMode,
    ContinuousModeConfig,
    SimulationConfig,
    SimulationMetrics,
    SimulationRunner,
    SimulationRunnerConfig,
)
from synthorg.client.feedback import BinaryFeedback
from synthorg.client.generators import ProceduralGenerator
from synthorg.engine.intake import IntakeEngine, IntakeResult

pytestmark = pytest.mark.integration


class _AlwaysAccept:
    async def process(self, request) -> IntakeResult:  # type: ignore[no-untyped-def]
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=f"task-{request.request_id[:6]}",
        )


def _make_client() -> AIClient:
    return AIClient(
        profile=ClientProfile(
            client_id="c1",
            name="Client",
            persona="Persona",
        ),
        generator=ProceduralGenerator(seed=1),
        feedback=BinaryFeedback(client_id="c1"),
    )


def _runner() -> SimulationRunner:
    return SimulationRunner(
        config=SimulationRunnerConfig(
            max_concurrent_tasks=2,
            task_timeout_sec=1.0,
            review_timeout_sec=1.0,
        ),
        intake_engine=IntakeEngine(strategy=_AlwaysAccept()),
    )


class TestContinuousMode:
    async def test_disabled_mode_returns_empty(self) -> None:
        mode = ContinuousMode(
            config=ContinuousModeConfig(enabled=False, request_interval_sec=0.01),
            runner=_runner(),
        )
        results = await mode.start(
            sim_config=SimulationConfig(
                project_id="proj-1",
                clients_per_round=1,
                requirements_per_client=1,
            ),
            clients=(_make_client(),),
        )
        assert results == []

    async def test_runs_until_stopped(self) -> None:
        mode = ContinuousMode(
            config=ContinuousModeConfig(
                enabled=True,
                request_interval_sec=0.01,
                max_concurrent_requests=1,
            ),
            runner=_runner(),
        )

        async def stopper() -> None:
            await mode.first_run_event.wait()
            mode.stop()

        async def starter() -> list[SimulationMetrics]:
            return await mode.start(
                sim_config=SimulationConfig(
                    project_id="proj-1",
                    clients_per_round=1,
                    requirements_per_client=1,
                ),
                clients=(_make_client(),),
            )

        _, results = await asyncio.gather(stopper(), starter())
        assert mode.runs_completed >= 1
        assert len(results) >= 1
        assert results[0].total_requirements >= 1
