"""Tests for category-based analytics."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.call_category import LLMCallCategory, OrchestrationAlertLevel
from synthorg.budget.category_analytics import (
    CategoryBreakdown,
    build_category_breakdown,
    compute_orchestration_ratio,
)
from synthorg.budget.coordination_config import OrchestrationAlertThresholds
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker

pytestmark = pytest.mark.timeout(30)


def _record(  # noqa: PLR0913
    *,
    category: LLMCallCategory | None = None,
    cost_usd: float = 0.01,
    input_tokens: int = 100,
    output_tokens: int = 50,
    agent_id: str = "alice",
    task_id: str = "task-001",
) -> CostRecord:
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        provider="test-provider",
        model="test-model-001",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        timestamp=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
        call_category=category,
    )


@pytest.mark.unit
class TestBuildCategoryBreakdown:
    """build_category_breakdown pure function."""

    def test_empty_records(self) -> None:
        result = build_category_breakdown([])
        assert result.productive_count == 0
        assert result.coordination_count == 0
        assert result.system_count == 0
        assert result.uncategorized_count == 0

    def test_all_productive(self) -> None:
        records = [
            _record(category=LLMCallCategory.PRODUCTIVE, cost_usd=0.01),
            _record(category=LLMCallCategory.PRODUCTIVE, cost_usd=0.02),
        ]
        result = build_category_breakdown(records)
        assert result.productive_count == 2
        assert result.productive_cost == 0.03
        assert result.productive_tokens == 300  # (100+50) * 2
        assert result.coordination_count == 0
        assert result.system_count == 0
        assert result.uncategorized_count == 0

    def test_mixed_categories(self) -> None:
        records = [
            _record(category=LLMCallCategory.PRODUCTIVE),
            _record(category=LLMCallCategory.COORDINATION),
            _record(category=LLMCallCategory.SYSTEM),
            _record(category=None),
        ]
        result = build_category_breakdown(records)
        assert result.productive_count == 1
        assert result.coordination_count == 1
        assert result.system_count == 1
        assert result.uncategorized_count == 1

    def test_all_uncategorized(self) -> None:
        records = [_record(category=None) for _ in range(3)]
        result = build_category_breakdown(records)
        assert result.uncategorized_count == 3
        assert result.productive_count == 0

    def test_token_accumulation(self) -> None:
        records = [
            _record(
                category=LLMCallCategory.PRODUCTIVE,
                input_tokens=200,
                output_tokens=100,
            ),
            _record(
                category=LLMCallCategory.PRODUCTIVE,
                input_tokens=300,
                output_tokens=150,
            ),
        ]
        result = build_category_breakdown(records)
        assert result.productive_tokens == 750  # 300 + 450

    def test_cost_precision(self) -> None:
        """Verify math.fsum is used for accurate summation."""
        # Many small values that could accumulate floating-point error
        records = [
            _record(category=LLMCallCategory.PRODUCTIVE, cost_usd=0.1)
            for _ in range(10)
        ]
        result = build_category_breakdown(records)
        assert result.productive_cost == 1.0


@pytest.mark.unit
class TestComputeOrchestrationRatio:
    """compute_orchestration_ratio pure function."""

    def test_zero_tokens(self) -> None:
        breakdown = CategoryBreakdown()
        result = compute_orchestration_ratio(breakdown)
        assert result.ratio == 0.0
        assert result.alert_level == OrchestrationAlertLevel.NORMAL
        assert result.total_tokens == 0

    def test_all_productive(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=1000,
            productive_count=10,
        )
        result = compute_orchestration_ratio(breakdown)
        assert result.ratio == 0.0
        assert result.alert_level == OrchestrationAlertLevel.NORMAL

    def test_high_coordination(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=200,
            coordination_tokens=600,
            system_tokens=200,
        )
        # overhead = 600 + 200 = 800, total = 1000, ratio = 0.8
        result = compute_orchestration_ratio(breakdown)
        assert result.ratio == 0.8
        assert result.alert_level == OrchestrationAlertLevel.CRITICAL

    def test_info_threshold(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=650,
            coordination_tokens=200,
            system_tokens=150,
        )
        # overhead = 350, total = 1000, ratio = 0.35
        result = compute_orchestration_ratio(breakdown)
        assert result.ratio == 0.35
        assert result.alert_level == OrchestrationAlertLevel.INFO

    def test_warning_threshold(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=400,
            coordination_tokens=350,
            system_tokens=250,
        )
        # overhead = 600, total = 1000, ratio = 0.6
        result = compute_orchestration_ratio(breakdown)
        assert result.ratio == 0.6
        assert result.alert_level == OrchestrationAlertLevel.WARNING

    def test_custom_thresholds(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=800,
            coordination_tokens=150,
            system_tokens=50,
        )
        # ratio = 200/1000 = 0.2
        thresholds = OrchestrationAlertThresholds(
            info=0.10,
            warn=0.15,
            critical=0.25,
        )
        result = compute_orchestration_ratio(
            breakdown,
            thresholds=thresholds,
        )
        assert result.ratio == 0.2
        assert result.alert_level == OrchestrationAlertLevel.WARNING

    def test_boundary_exactly_at_info(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=700,
            coordination_tokens=300,
        )
        # ratio = 300/1000 = 0.30 — exactly at info threshold
        result = compute_orchestration_ratio(breakdown)
        assert result.ratio == 0.3
        assert result.alert_level == OrchestrationAlertLevel.INFO

    def test_includes_uncategorized_in_total(self) -> None:
        breakdown = CategoryBreakdown(
            productive_tokens=500,
            coordination_tokens=200,
            uncategorized_tokens=300,
        )
        # overhead = 200, total = 1000, ratio = 0.2
        result = compute_orchestration_ratio(breakdown)
        assert result.total_tokens == 1000
        assert result.ratio == 0.2


@pytest.mark.unit
class TestCostTrackerCategoryQueries:
    """CostTracker.get_category_breakdown and get_orchestration_ratio."""

    async def test_get_category_breakdown_empty(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        result = await cost_tracker.get_category_breakdown()
        assert result.productive_count == 0
        assert result.uncategorized_count == 0

    async def test_get_category_breakdown_with_records(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        await cost_tracker.record(
            _record(category=LLMCallCategory.PRODUCTIVE),
        )
        await cost_tracker.record(
            _record(category=LLMCallCategory.COORDINATION),
        )
        result = await cost_tracker.get_category_breakdown()
        assert result.productive_count == 1
        assert result.coordination_count == 1

    async def test_get_category_breakdown_filter_by_agent(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        await cost_tracker.record(
            _record(
                category=LLMCallCategory.PRODUCTIVE,
                agent_id="alice",
            ),
        )
        await cost_tracker.record(
            _record(
                category=LLMCallCategory.PRODUCTIVE,
                agent_id="bob",
            ),
        )
        result = await cost_tracker.get_category_breakdown(
            agent_id="alice",
        )
        assert result.productive_count == 1

    async def test_get_category_breakdown_filter_by_task(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        await cost_tracker.record(
            _record(
                category=LLMCallCategory.PRODUCTIVE,
                task_id="task-001",
            ),
        )
        await cost_tracker.record(
            _record(
                category=LLMCallCategory.PRODUCTIVE,
                task_id="task-002",
            ),
        )
        result = await cost_tracker.get_category_breakdown(
            task_id="task-001",
        )
        assert result.productive_count == 1

    async def test_get_orchestration_ratio_empty(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        result = await cost_tracker.get_orchestration_ratio()
        assert result.ratio == 0.0
        assert result.alert_level == OrchestrationAlertLevel.NORMAL

    async def test_get_orchestration_ratio_with_records(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        for _ in range(7):
            await cost_tracker.record(
                _record(category=LLMCallCategory.PRODUCTIVE),
            )
        for _ in range(3):
            await cost_tracker.record(
                _record(category=LLMCallCategory.COORDINATION),
            )
        result = await cost_tracker.get_orchestration_ratio()
        assert result.ratio == 0.3
        assert result.alert_level == OrchestrationAlertLevel.INFO

    async def test_invalid_time_range(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        with pytest.raises(ValueError, match="must be before"):
            await cost_tracker.get_category_breakdown(
                start=datetime(2026, 3, 1, tzinfo=UTC),
                end=datetime(2026, 2, 1, tzinfo=UTC),
            )
