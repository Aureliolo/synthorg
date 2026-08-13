"""Unit tests for ParetoAnalyzer."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import pytest

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.budget.config import AutoDowngradeConfig, BudgetConfig
from synthorg.budget.model_capability import ModelCapabilityMap
from synthorg.budget.pareto import (
    ParetoAnalyzer,
    ParetoFrontier,
    RoleAssignment,
)
from synthorg.core.types import NotBlankStr
from tests._shared import (
    FIXTURE_SOURCE,
    FakeCapabilityBenchmarkScoreProvider,
    FakeClock,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


class _AnyModelScoreProvider:
    """Measured-style provider that scores any model id.

    A real measured repository keyed by arbitrary operator ids; used to
    show the ``ModelCapabilityMap`` lets a non-archetype current model resolve a
    downgrade candidate (the stub provider cannot score such an id).
    """

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore:
        score = 90.0 if "flagship" in model_id else 70.0
        return BenchmarkScore(
            score=score,
            confidence_lower=score - 2.0,
            confidence_upper=score + 2.0,
            source=NotBlankStr("benchmark:measured-v1"),
            last_updated=_NOW,
        )

    async def list_scores(self) -> dict[NotBlankStr, BenchmarkScore]:
        return {}


def _config() -> BudgetConfig:
    return BudgetConfig(
        total_monthly=100.0,
        auto_downgrade=AutoDowngradeConfig(
            enabled=False,
            downgrade_map=(("expert", "capable"), ("capable", "basic")),
        ),
        forecast_static_prior_per_turn_expert=0.10,
        forecast_static_prior_per_turn_capable=0.03,
        forecast_static_prior_per_turn_basic=0.005,
    )


def _assignments(
    *,
    items: Sequence[RoleAssignment],
) -> Callable[[], Awaitable[Sequence[RoleAssignment]]]:
    async def _lookup() -> Sequence[RoleAssignment]:
        return items

    return _lookup


class TestParetoAnalyzer:
    async def test_empty_assignments_returns_empty_frontier(self) -> None:
        analyzer = ParetoAnalyzer(
            benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
            budget_config=_config(),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert isinstance(frontier, ParetoFrontier)
        assert frontier.points == ()
        assert frontier.generated_at == _NOW
        assert frontier.source == "no-measured-scores"

    async def test_single_assignment_emits_frontier_point(self) -> None:
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Backend Engineer",
            current_model="example-expert-001",
            current_cost_per_task=1.00,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert len(frontier.points) == 1
        point = frontier.points[0]
        assert point.role_label == "Backend Engineer"
        assert point.candidate_model == "example-capable-001"
        assert point.cost_saving_pct > 0
        assert point.quality_delta_pct > 0
        assert point.source == FIXTURE_SOURCE

    async def test_model_tier_map_resolves_non_archetype_current_model(self) -> None:
        # A non-archetype id is skipped by the heuristic; an operator
        # override map resolves its tier so the downgrade candidate is
        # evaluated and a frontier point is emitted.
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Backend Engineer",
            current_model="acme-flagship-v3",
            current_cost_per_task=1.00,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=_AnyModelScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            model_capability_map=ModelCapabilityMap(
                overrides={NotBlankStr("acme-flagship-v3"): "expert"}
            ),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert len(frontier.points) == 1
        assert frontier.points[0].current_model == "acme-flagship-v3"
        assert frontier.points[0].candidate_model == "example-capable-001"

    async def test_non_archetype_model_skipped_without_tier_map(self) -> None:
        # Control: the same non-archetype id resolves no tier without the
        # override map, so the role is skipped (no frontier point).
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Backend Engineer",
            current_model="acme-flagship-v3",
            current_cost_per_task=1.00,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=_AnyModelScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert frontier.points == ()

    async def test_zero_cost_role_skipped(self) -> None:
        assignment = RoleAssignment(
            role_id="role-1",
            role_label="Backend Engineer",
            current_model="example-expert-001",
            current_cost_per_task=0.0,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
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
            current_model="example-local-basic-001",
            current_cost_per_task=0.001,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
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
                current_model="example-expert-001",
                current_cost_per_task=2.00,
            ),
            RoleAssignment(
                role_id="role-medium",
                role_label="Medium User",
                current_model="example-capable-001",
                current_cost_per_task=0.50,
            ),
        ]
        analyzer = ParetoAnalyzer(
            benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
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
            current_model="example-capable-001",
            current_cost_per_task=0.50,
        )
        analyzer = ParetoAnalyzer(
            benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
            budget_config=_config(),
            assignment_lookup=_assignments(items=[assignment]),
            clock=FakeClock(start=_NOW).now,
        )
        frontier = await analyzer.analyse()
        assert frontier.points
        for point in frontier.points:
            assert 0 <= point.quality_delta_pct <= 100
            assert 0 <= point.cost_saving_pct <= 100
