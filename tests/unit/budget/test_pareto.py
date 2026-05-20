"""Unit tests for ParetoAnalyzer and StubBenchmarkScoreProvider."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import pytest

from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.budget.config import AutoDowngradeConfig, BudgetConfig
from synthorg.budget.pareto import (
    ParetoAnalyzer,
    ParetoFrontier,
    RoleAssignment,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _config() -> BudgetConfig:
    return BudgetConfig(
        total_monthly=100.0,
        auto_downgrade=AutoDowngradeConfig(
            enabled=False,
            downgrade_map=(("large", "medium"), ("medium", "small")),
        ),
        forecast_static_prior_per_turn_large=0.10,
        forecast_static_prior_per_turn_medium=0.03,
        forecast_static_prior_per_turn_small=0.005,
    )


def _assignments(
    *,
    items: Sequence[RoleAssignment],
) -> Callable[[], Awaitable[Sequence[RoleAssignment]]]:
    async def _lookup() -> Sequence[RoleAssignment]:
        return items

    return _lookup


class TestStubBenchmarkScoreProvider:
    async def test_known_tier_returns_calibrated_score(self) -> None:
        provider = StubBenchmarkScoreProvider()
        score = await provider.get_score("example-large-001")
        assert score is not None
        assert 0 <= score.score <= 100
        assert score.source == "stub:calibrated-v1"

    async def test_unknown_model_returns_none(self) -> None:
        provider = StubBenchmarkScoreProvider()
        assert await provider.get_score("totally-made-up") is None

    async def test_list_scores_keyed_by_canonical_model_id(self) -> None:
        provider = StubBenchmarkScoreProvider()
        scores = await provider.list_scores()
        assert {
            "example-large-001",
            "example-medium-001",
            "example-small-001",
            "example-local-small-001",
        } <= set(scores)


class TestParetoAnalyzer:
    async def test_empty_assignments_returns_empty_frontier(self) -> None:
        analyzer = ParetoAnalyzer(
            benchmark_provider=StubBenchmarkScoreProvider(),
            budget_config=_config(),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert isinstance(frontier, ParetoFrontier)
        assert frontier.points == ()
        assert frontier.generated_at == _NOW
        assert "stub:calibrated-v1" in frontier.source

    async def test_single_assignment_emits_frontier_point(self) -> None:
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Backend Engineer",
            current_model="example-large-001",
            current_cost_per_task=1.00,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=StubBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert len(frontier.points) == 1
        point = frontier.points[0]
        assert point.role_label == "Backend Engineer"
        assert point.candidate_model == "example-medium-001"
        assert point.cost_saving_pct > 0
        assert point.quality_delta_pct > 0
        assert point.source == "stub:calibrated-v1"

    async def test_zero_cost_role_skipped(self) -> None:
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Backend Engineer",
            current_model="example-large-001",
            current_cost_per_task=0.0,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=StubBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert frontier.points == ()

    async def test_no_downgrade_path_skipped(self) -> None:
        # `local-small` has no downgrade target in the default map.
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Local Worker",
            current_model="example-local-small-001",
            current_cost_per_task=0.001,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=StubBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert frontier.points == ()

    async def test_frontier_sorted_by_cost_saving_descending(self) -> None:
        assignments = [
            RoleAssignment(
                role_id="role-large",
                role_label="Large User",
                current_model="example-large-001",
                current_cost_per_task=2.00,
            ),
            RoleAssignment(
                role_id="role-medium",
                role_label="Medium User",
                current_model="example-medium-001",
                current_cost_per_task=0.50,
            ),
        ]
        analyzer = ParetoAnalyzer(
            benchmark_provider=StubBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=assignments),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert len(frontier.points) >= 2
        for i in range(len(frontier.points) - 1):
            assert (
                frontier.points[i].cost_saving_pct
                >= frontier.points[i + 1].cost_saving_pct
            )

    async def test_quality_delta_within_bounds(self) -> None:
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Engineer",
            current_model="example-medium-001",
            current_cost_per_task=0.50,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=StubBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert frontier.points
        for point in frontier.points:
            assert 0 <= point.quality_delta_pct <= 100
            assert 0 <= point.cost_saving_pct <= 100
