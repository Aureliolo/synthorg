"""Property-based tests for CallAnalyticsService aggregation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.budget.call_analytics import CallAnalyticsService
from synthorg.budget.call_analytics_config import CallAnalyticsConfig
from synthorg.budget.call_category import OrchestrationAlertLevel
from synthorg.budget.category_analytics import OrchestrationRatio
from synthorg.budget.cost_record import CostRecord
from synthorg.core.completion_enums import FinishReason


def _record(
    *,
    retry_count: int | None = None,
    cache_hit: bool | None = None,
    latency_ms: float | None = None,
    success: bool | None = None,
    finish_reason: FinishReason = FinishReason.STOP,
) -> CostRecord:
    return CostRecord(
        agent_id="agent-1",
        task_id="task-1",
        provider="test-provider",
        model="test-model",
        input_tokens=100,
        output_tokens=50,
        cost=0.01,
        currency="EUR",
        timestamp=datetime(2026, 4, 1, tzinfo=UTC),
        retry_count=retry_count,
        cache_hit=cache_hit,
        latency_ms=latency_ms,
        success=success,
        finish_reason=finish_reason,
    )


def _make_service(records: tuple[CostRecord, ...]) -> CallAnalyticsService:
    tracker = AsyncMock()
    tracker.get_records = AsyncMock(return_value=records)
    tracker.get_orchestration_ratio = AsyncMock(
        return_value=OrchestrationRatio(
            ratio=0.0,
            alert_level=OrchestrationAlertLevel.NORMAL,
            total_tokens=0,
            productive_tokens=0,
            coordination_tokens=0,
            system_tokens=0,
        )
    )
    return CallAnalyticsService(
        cost_tracker=tracker,
        config=CallAnalyticsConfig(),
    )


_record_strategy = st.builds(
    lambda retry, cache, latency: _record(
        retry_count=retry,
        cache_hit=cache,
        latency_ms=latency,
    ),
    retry=st.one_of(st.none(), st.integers(min_value=0, max_value=10)),
    cache=st.one_of(st.none(), st.booleans()),
    latency=st.one_of(
        st.none(), st.floats(min_value=0.0, max_value=10000.0, allow_nan=False)
    ),
)


@pytest.mark.unit
class TestCallAnalyticsProperties:
    """Invariants for CallAnalyticsService.get_aggregation()."""

    @given(st.lists(_record_strategy, max_size=20))
    async def test_retry_rate_in_unit_interval(self, records: list[CostRecord]) -> None:
        """retry_rate is always in [0.0, 1.0]."""
        service = _make_service(tuple(records))
        agg = await service.get_aggregation()
        assert 0.0 <= agg.retry_rate <= 1.0

    @given(st.lists(_record_strategy, max_size=20))
    async def test_cache_hit_rate_in_unit_interval(
        self, records: list[CostRecord]
    ) -> None:
        """cache_hit_rate is None or in [0.0, 1.0]."""
        service = _make_service(tuple(records))
        agg = await service.get_aggregation()
        if agg.cache_hit_rate is not None:
            assert 0.0 <= agg.cache_hit_rate <= 1.0

    @given(st.lists(_record_strategy, max_size=20))
    async def test_success_failure_sum_le_total(
        self, records: list[CostRecord]
    ) -> None:
        """success_count + failure_count <= total_calls (success=None excluded)."""
        service = _make_service(tuple(records))
        agg = await service.get_aggregation()
        assert agg.success_count + agg.failure_count <= agg.total_calls

    @given(st.lists(_record_strategy, max_size=20))
    async def test_total_calls_matches_input(self, records: list[CostRecord]) -> None:
        """total_calls equals the number of records fetched."""
        service = _make_service(tuple(records))
        agg = await service.get_aggregation()
        assert agg.total_calls == len(records)
