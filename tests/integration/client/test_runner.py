"""Integration tests for SimulationRunner."""

import pytest

from synthorg.client import (
    AIClient,
    ClientInterface,
    ClientProfile,
    GenerationContext,
    SimulationConfig,
    SimulationRunner,
    SimulationRunnerConfig,
    TaskRequirement,
)
from synthorg.client.feedback import BinaryFeedback, ScoredFeedback
from synthorg.client.generators import ProceduralGenerator
from synthorg.client.report import DetailedReport
from synthorg.engine.intake import IntakeEngine, IntakeResult

pytestmark = pytest.mark.integration


class _CountingStrategy:
    def __init__(self, *, accept_pattern: tuple[bool, ...]) -> None:
        self._pattern = accept_pattern
        self._index = 0

    async def process(self, request) -> IntakeResult:  # type: ignore[no-untyped-def]
        decision = self._pattern[self._index % len(self._pattern)]
        self._index += 1
        if decision:
            return IntakeResult.accepted_result(
                request_id=request.request_id,
                task_id=f"task-{self._index}",
            )
        return IntakeResult.rejected_result(
            request_id=request.request_id,
            reason="test-driven rejection",
        )


def _make_client(*, client_id: str, strictness: float = 0.5) -> AIClient:
    return AIClient(
        profile=ClientProfile(
            client_id=client_id,
            name=client_id,
            persona=f"Persona {client_id}",
            strictness_level=strictness,
        ),
        generator=ProceduralGenerator(
            seed=sum(ord(c) for c in client_id) & 0xFFFF,
        ),
        feedback=BinaryFeedback(client_id=client_id),
    )


def _runner_config() -> SimulationRunnerConfig:
    return SimulationRunnerConfig(
        max_concurrent_tasks=4,
        task_timeout_sec=5.0,
        review_timeout_sec=5.0,
    )


class TestSimulationRunnerBasic:
    async def test_run_returns_metrics_and_report(self) -> None:
        intake = IntakeEngine(
            strategy=_CountingStrategy(accept_pattern=(True, True, False, True))
        )
        runner = SimulationRunner(
            config=_runner_config(),
            intake_engine=intake,
            report_strategy=DetailedReport(),
        )
        clients: tuple[ClientInterface, ...] = (
            _make_client(client_id="c1"),
            _make_client(client_id="c2"),
            _make_client(client_id="c3"),
        )
        metrics, report = await runner.run(
            sim_config=SimulationConfig(
                project_id="proj-1",
                rounds=2,
                clients_per_round=3,
                requirements_per_client=1,
            ),
            clients=clients,
        )
        assert metrics.total_requirements == 6
        assert metrics.total_tasks_created <= 6
        assert metrics.total_tasks_created >= 1
        assert report is not None
        assert report["format"] == "detailed"
        per_round = report["per_round"]
        assert isinstance(per_round, list)
        assert len(per_round) == 2

    async def test_run_without_report_strategy(self) -> None:
        intake = IntakeEngine(strategy=_CountingStrategy(accept_pattern=(True,)))
        runner = SimulationRunner(config=_runner_config(), intake_engine=intake)
        metrics, report = await runner.run(
            sim_config=SimulationConfig(
                project_id="proj-1",
                clients_per_round=1,
                requirements_per_client=1,
            ),
            clients=(_make_client(client_id="c1"),),
        )
        assert metrics.total_requirements == 1
        assert report is None

    async def test_run_requires_clients(self) -> None:
        intake = IntakeEngine(strategy=_CountingStrategy(accept_pattern=(True,)))
        runner = SimulationRunner(config=_runner_config(), intake_engine=intake)
        with pytest.raises(ValueError, match="at least one client"):
            await runner.run(
                sim_config=SimulationConfig(project_id="proj-1"),
                clients=(),
            )


class _EmptyGenerator:
    async def generate(self, context: GenerationContext) -> tuple[TaskRequirement, ...]:
        del context
        return ()


class TestSimulationRunnerEdgeCases:
    async def test_clients_that_decline_are_skipped(self) -> None:
        profile = ClientProfile(
            client_id="declining",
            name="Declining",
            persona="Never participates",
        )
        declining = AIClient(
            profile=profile,
            generator=_EmptyGenerator(),
            feedback=ScoredFeedback(client_id="declining"),
        )
        intake = IntakeEngine(strategy=_CountingStrategy(accept_pattern=(True,)))
        runner = SimulationRunner(config=_runner_config(), intake_engine=intake)
        metrics, _ = await runner.run(
            sim_config=SimulationConfig(
                project_id="proj-1",
                clients_per_round=1,
                requirements_per_client=1,
            ),
            clients=(declining,),
        )
        assert metrics.total_requirements == 0
        assert metrics.total_tasks_created == 0

    async def test_rejected_intake_counts_zero_tasks(self) -> None:
        intake = IntakeEngine(strategy=_CountingStrategy(accept_pattern=(False,)))
        runner = SimulationRunner(config=_runner_config(), intake_engine=intake)
        metrics, _ = await runner.run(
            sim_config=SimulationConfig(
                project_id="proj-1",
                clients_per_round=2,
                requirements_per_client=1,
            ),
            clients=(
                _make_client(client_id="c1"),
                _make_client(client_id="c2"),
            ),
        )
        assert metrics.total_requirements == 2
        assert metrics.total_tasks_created == 0
        assert metrics.tasks_accepted == 0
        # Round metrics populated
        assert len(metrics.round_metrics) == 1


class TestRunnerRoundSnapshots:
    async def test_round_snapshots_match_rounds(self) -> None:
        intake = IntakeEngine(strategy=_CountingStrategy(accept_pattern=(True,)))
        runner = SimulationRunner(config=_runner_config(), intake_engine=intake)
        metrics, _ = await runner.run(
            sim_config=SimulationConfig(
                project_id="proj-1",
                rounds=3,
                clients_per_round=1,
                requirements_per_client=1,
            ),
            clients=(_make_client(client_id="c1"),),
        )
        assert len(metrics.round_metrics) == 3
        for snapshot in metrics.round_metrics:
            # Each round has a stable "round_number" key.
            assert isinstance(snapshot, dict)
            assert "round_number" in snapshot
