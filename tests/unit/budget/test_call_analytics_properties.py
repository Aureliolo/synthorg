"""Property-based tests for CallAnalyticsService aggregation."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.budget.call_analytics import CallAnalyticsService
from synthorg.budget.call_analytics_config import CallAnalyticsConfig
from synthorg.budget.call_category import OrchestrationAlertLevel
from synthorg.budget.category_analytics import OrchestrationRatio
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.completion_enums import FinishReason
from tests._shared import mock_of


def _record(
    *,
    retry_count: int | None = None,
    cache_read_input_tokens: int = 0,
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
        cache_read_input_tokens=cache_read_input_tokens,
        latency_ms=latency_ms,
        success=success,
        finish_reason=finish_reason,
    )


def _make_service(records: tuple[CostRecord, ...]) -> CallAnalyticsService:
    tracker = mock_of[CostTrackerProtocol]()
    tracker.get_records.return_value = records
    tracker.collect_records.return_value = records
    tracker.get_orchestration_ratio.return_value = OrchestrationRatio(
        ratio=0.0,
        alert_level=OrchestrationAlertLevel.NORMAL,
        total_tokens=0,
        productive_tokens=0,
        coordination_tokens=0,
        system_tokens=0,
    )
    return CallAnalyticsService(
        cost_tracker=tracker,
        config=CallAnalyticsConfig(),
    )


_record_strategy = st.builds(
    lambda retry, cache, latency: _record(
        retry_count=retry,
        cache_read_input_tokens=cache,
        latency_ms=latency,
    ),
    retry=st.one_of(st.none(), st.integers(min_value=0, max_value=10)),
    # Never above the record's own input: a cached read larger than what
    # was sent is refused at construction.
    cache=st.integers(min_value=0, max_value=100),
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
    async def test_cached_input_share_in_unit_interval(
        self, records: list[CostRecord]
    ) -> None:
        """cached_input_share is None or in [0.0, 1.0].

        The strategy never draws a cached count above the record's own input,
        because a record is refused at construction otherwise; the share is
        a ratio of two sums, so this pins that summing keeps it in range.
        """
        service = _make_service(tuple(records))
        agg = await service.get_aggregation()
        if agg.cached_input_share is not None:
            assert 0.0 <= agg.cached_input_share <= 1.0

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
