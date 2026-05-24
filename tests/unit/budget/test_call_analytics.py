# mypy: disable-error-code="explicit-any"
"""Tests for CallAnalyticsService aggregation and alerting."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.call_analytics_config import CallAnalyticsConfig, RetryAlertConfig
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.providers.enums import FinishReason


def _record(  # noqa: PLR0913
    *,
    agent_id: str = "agent-1",
    task_id: str = "task-1",
    provider: str = "test-provider",
    model: str = "test-model",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost: float = 0.01,
    latency_ms: float | None = None,
    cache_hit: bool | None = None,
    retry_count: int | None = None,
    retry_reason: str | None = None,
    finish_reason: FinishReason = FinishReason.STOP,
    success: bool | None = True,
    call_category: LLMCallCategory | None = None,
) -> CostRecord:
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        currency="EUR",
        timestamp=datetime(2026, 4, 1, tzinfo=UTC),
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        retry_count=retry_count,
        retry_reason=retry_reason,
        finish_reason=finish_reason,
        success=success,
        call_category=call_category,
    )


def _make_service(
    records: tuple[CostRecord, ...] = (),
    *,
    config: CallAnalyticsConfig | None = None,
    notification_dispatcher: Any = None,
) -> Any:
    from synthorg.budget.call_analytics import CallAnalyticsService

    tracker = AsyncMock()
    tracker.get_records = AsyncMock(return_value=records)
    tracker.get_orchestration_ratio = AsyncMock(
        side_effect=lambda **_kw: _dummy_orchestration_ratio()
    )

    return CallAnalyticsService(
        cost_tracker=tracker,
        config=config or CallAnalyticsConfig(),
        notification_dispatcher=notification_dispatcher,
    )


def _dummy_orchestration_ratio() -> Any:
    from synthorg.budget.call_category import OrchestrationAlertLevel
    from synthorg.budget.category_analytics import OrchestrationRatio

    return OrchestrationRatio(
        ratio=0.0,
        alert_level=OrchestrationAlertLevel.NORMAL,
        total_tokens=0,
        productive_tokens=0,
        coordination_tokens=0,
        system_tokens=0,
    )


@pytest.mark.unit
class TestP95:
    """Dedicated tests for the _p95 percentile helper."""

    def test_single_value(self) -> None:
        from synthorg.budget.call_analytics import _p95

        assert _p95([42.0]) == 42.0

    def test_two_values(self) -> None:
        from synthorg.budget.call_analytics import _p95

        result = _p95([10.0, 20.0])
        assert result == pytest.approx(19.5)

    def test_many_values(self) -> None:
        from synthorg.budget.call_analytics import _p95

        values = list(range(1, 101))  # 1..100
        result = _p95([float(v) for v in values])
        assert result == pytest.approx(95.05, abs=0.1)

    def test_unsorted_input(self) -> None:
        from synthorg.budget.call_analytics import _p95

        result = _p95([50.0, 10.0, 90.0, 30.0, 70.0])
        # sorted: [10, 30, 50, 70, 90], p95 index = 0.95 * 4 = 3.8
        # interpolate: 70 + 0.8 * (90 - 70) = 86.0
        assert result == pytest.approx(86.0)


@pytest.mark.unit
class TestGetAggregationEmpty:
    """get_aggregation on empty record set."""

    async def test_empty_returns_zero_totals(self) -> None:
        service = _make_service(())
        agg = await service.get_aggregation()
        assert agg.total_calls == 0
        assert agg.success_count == 0
        assert agg.failure_count == 0
        assert agg.retry_count == 0
        assert agg.retry_rate == 0.0
        assert agg.cache_hit_count == 0
        assert agg.cache_hit_rate is None
        assert agg.avg_latency_ms is None
        assert agg.p95_latency_ms is None
        assert agg.by_finish_reason == ()


@pytest.mark.unit
class TestGetAggregationCounts:
    """get_aggregation correctly counts calls."""

    async def test_total_calls(self) -> None:
        records = tuple(_record() for _ in range(5))
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.total_calls == 5

    async def test_success_failure_counts(self) -> None:
        records = (
            _record(success=True),
            _record(success=True),
            _record(success=False, finish_reason=FinishReason.ERROR),
        )
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.success_count == 2
        assert agg.failure_count == 1

    async def test_retry_count_and_rate(self) -> None:
        """retry_count = calls with >=1 retry; retry_rate = retry_count / total."""
        records = (
            _record(retry_count=0),
            _record(retry_count=2),
            _record(retry_count=0),
            _record(retry_count=1),
        )
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.retry_count == 2
        assert agg.retry_rate == pytest.approx(0.5)

    async def test_cache_hit_rate(self) -> None:
        records = (
            _record(cache_hit=True),
            _record(cache_hit=False),
            _record(cache_hit=True),
        )
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.cache_hit_count == 2
        assert agg.cache_hit_rate == pytest.approx(2 / 3)

    async def test_cache_hit_rate_none_when_no_cache_data(self) -> None:
        records = (_record(cache_hit=None),)
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.cache_hit_rate is None

    async def test_avg_latency(self) -> None:
        records = (
            _record(latency_ms=100.0),
            _record(latency_ms=200.0),
        )
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.avg_latency_ms == pytest.approx(150.0)

    async def test_latency_none_when_no_data(self) -> None:
        records = (_record(latency_ms=None),)
        service = _make_service(records)
        agg = await service.get_aggregation()
        assert agg.avg_latency_ms is None

    async def test_by_finish_reason(self) -> None:
        records = (
            _record(finish_reason=FinishReason.STOP),
            _record(finish_reason=FinishReason.STOP),
            _record(finish_reason=FinishReason.ERROR),
        )
        service = _make_service(records)
        agg = await service.get_aggregation()
        reason_dict = dict(agg.by_finish_reason)
        assert reason_dict["stop"] == 2
        assert reason_dict["error"] == 1


@pytest.mark.unit
class TestCheckAlerts:
    """check_alerts dispatches when thresholds are crossed."""

    async def test_no_alerts_when_disabled(self) -> None:
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        service = _make_service(
            config=CallAnalyticsConfig(enabled=False),
            notification_dispatcher=dispatcher,
        )
        records = tuple(_record(retry_count=5) for _ in range(10))
        await service.check_alerts(records)
        dispatcher.dispatch.assert_not_called()

    async def test_retry_rate_alert_dispatched(self) -> None:
        """Alert fires when retry_rate exceeds warn_rate."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        config = CallAnalyticsConfig(retry_alerts=RetryAlertConfig(warn_rate=0.10))
        service = _make_service(config=config, notification_dispatcher=dispatcher)

        # 5/10 calls had retries -> retry_rate = 0.50 > 0.10
        records = (
            *[_record(retry_count=1) for _ in range(5)],
            *[_record(retry_count=0) for _ in range(5)],
        )
        await service.check_alerts(records)
        dispatcher.dispatch.assert_called_once()

    async def test_no_retry_alert_below_threshold(self) -> None:
        """No alert when retry_rate is below warn_rate."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        config = CallAnalyticsConfig(retry_alerts=RetryAlertConfig(warn_rate=0.50))
        service = _make_service(config=config, notification_dispatcher=dispatcher)

        # 1/10 calls had retries -> retry_rate = 0.10 < 0.50
        records = (
            _record(retry_count=1),
            *[_record(retry_count=0) for _ in range(9)],
        )
        await service.check_alerts(records)
        dispatcher.dispatch.assert_not_called()

    async def test_no_dispatcher_no_crash(self) -> None:
        """Without dispatcher, check_alerts runs without error."""
        service = _make_service(notification_dispatcher=None)
        records = tuple(_record(retry_count=1) for _ in range(10))
        await service.check_alerts(records)

    async def test_empty_records_no_alert(self) -> None:
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        service = _make_service(notification_dispatcher=dispatcher)
        await service.check_alerts(())
        dispatcher.dispatch.assert_not_called()
