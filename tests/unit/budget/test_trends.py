"""Tests for time-series trend bucketing and budget forecast projections."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.trends import (
    BucketSize,
    TrendMetric,
    TrendPeriod,
    bucket_cost_records,
    bucket_success_rate,
    bucket_task_completions,
    generate_bucket_starts,
    period_to_timedelta,
    project_daily_spend,
    resolve_bucket_size,
)
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.hr.performance.models import TaskMetricRecord

# ── Helpers ────────────────────────────────────────────────────


def _cost_record(
    *,
    timestamp: datetime,
    cost: float = 0.01,
    agent_id: str = "agent-a",
    task_id: str = "task-001",
) -> CostRecord:
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        provider="test-provider",
        model="test-small-001",
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        currency="EUR",
        timestamp=timestamp,
    )


def _task_metric(
    *,
    completed_at: datetime,
    is_success: bool = True,
    agent_id: str = "agent-a",
    task_id: str = "task-001",
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=agent_id,
        task_id=task_id,
        task_type=TaskType.DEVELOPMENT,
        completed_at=completed_at,
        is_success=is_success,
        duration_seconds=10.0,
        cost=0.01,
        currency="EUR",
        turns_used=2,
        tokens_used=150,
        complexity=Complexity.SIMPLE,
    )


# ── Resolver tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestResolvers:
    """resolve_bucket_size and period_to_timedelta."""

    @pytest.mark.parametrize(
        ("period", "expected"),
        [
            (TrendPeriod.SEVEN_DAYS, BucketSize.HOUR),
            (TrendPeriod.THIRTY_DAYS, BucketSize.DAY),
            (TrendPeriod.NINETY_DAYS, BucketSize.DAY),
        ],
    )
    def test_resolve_bucket_size(
        self,
        period: TrendPeriod,
        expected: BucketSize,
    ) -> None:
        assert resolve_bucket_size(period) == expected

    @pytest.mark.parametrize(
        ("period", "expected"),
        [
            (TrendPeriod.SEVEN_DAYS, timedelta(days=7)),
            (TrendPeriod.THIRTY_DAYS, timedelta(days=30)),
            (TrendPeriod.NINETY_DAYS, timedelta(days=90)),
        ],
    )
    def test_period_to_timedelta(
        self,
        period: TrendPeriod,
        expected: timedelta,
    ) -> None:
        assert period_to_timedelta(period) == expected


# ── bucket_cost_records tests ──────────────────────────────────


@pytest.mark.unit
class TestBucketCostRecords:
    """bucket_cost_records pure function."""

    def test_empty_records_daily(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 4, 0, 0, 0, tzinfo=UTC)
        result = bucket_cost_records([], start, end, BucketSize.DAY)
        assert len(result) == 3
        assert all(p.value == 0.0 for p in result)

    def test_empty_records_hourly(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 3, 0, 0, tzinfo=UTC)
        result = bucket_cost_records([], start, end, BucketSize.HOUR)
        assert len(result) == 3
        assert all(p.value == 0.0 for p in result)

    def test_daily_bucketing(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 4, 0, 0, 0, tzinfo=UTC)
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=1.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC), cost=2.0
            ),
            _cost_record(timestamp=datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC), cost=0.5),
            # Day 3 has no records
        ]
        result = bucket_cost_records(records, start, end, BucketSize.DAY)
        assert len(result) == 3
        assert result[0].value == 3.0  # day 1: 1.0 + 2.0
        assert result[1].value == 0.5  # day 2: 0.5
        assert result[2].value == 0.0  # day 3: empty

    def test_hourly_bucketing(self) -> None:
        start = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 13, 0, 0, tzinfo=UTC)
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 15, 0, tzinfo=UTC), cost=0.1
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 45, 0, tzinfo=UTC), cost=0.2
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 1, 12, 30, 0, tzinfo=UTC), cost=0.5
            ),
        ]
        result = bucket_cost_records(records, start, end, BucketSize.HOUR)
        assert len(result) == 3
        assert result[0].value == pytest.approx(0.3)  # 10:00-11:00
        assert result[1].value == 0.0  # 11:00-12:00
        assert result[2].value == 0.5  # 12:00-13:00

    def test_records_outside_range_excluded(self) -> None:
        start = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 23, 0, 0, tzinfo=UTC), cost=1.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC), cost=0.5
            ),
            _cost_record(timestamp=datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC), cost=1.0),
        ]
        result = bucket_cost_records(records, start, end, BucketSize.DAY)
        assert len(result) == 1
        assert result[0].value == 0.5

    def test_single_record(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 15, 30, 0, tzinfo=UTC), cost=42.0
            ),
        ]
        result = bucket_cost_records(records, start, end, BucketSize.DAY)
        assert len(result) == 1
        assert result[0].value == 42.0
        assert result[0].timestamp == datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)

    def test_data_points_are_sorted(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 5, 0, 0, 0, tzinfo=UTC)
        result = bucket_cost_records([], start, end, BucketSize.DAY)
        timestamps = [p.timestamp for p in result]
        assert timestamps == sorted(timestamps)

    def test_data_points_are_frozen(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        result = bucket_cost_records([], start, end, BucketSize.DAY)
        with pytest.raises(ValidationError):
            result[0].value = 99.0  # type: ignore[misc]


# ── bucket_task_completions tests ──────────────────────────────


@pytest.mark.unit
class TestBucketTaskCompletions:
    """bucket_task_completions pure function."""

    def test_empty_records(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
        result = bucket_task_completions([], start, end, BucketSize.DAY)
        assert len(result) == 2
        assert all(p.value == 0.0 for p in result)

    def test_counts_per_day(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(completed_at=datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)),
            _task_metric(completed_at=datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC)),
            _task_metric(completed_at=datetime(2026, 3, 1, 17, 0, 0, tzinfo=UTC)),
            _task_metric(completed_at=datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC)),
        ]
        result = bucket_task_completions(records, start, end, BucketSize.DAY)
        assert len(result) == 2
        assert result[0].value == 3.0  # day 1
        assert result[1].value == 1.0  # day 2

    def test_counts_per_hour(self) -> None:
        start = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(completed_at=datetime(2026, 3, 1, 10, 15, 0, tzinfo=UTC)),
            _task_metric(completed_at=datetime(2026, 3, 1, 10, 45, 0, tzinfo=UTC)),
        ]
        result = bucket_task_completions(records, start, end, BucketSize.HOUR)
        assert len(result) == 2
        assert result[0].value == 2.0  # 10:00-11:00
        assert result[1].value == 0.0  # 11:00-12:00

    def test_excludes_out_of_range(self) -> None:
        start = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(completed_at=datetime(2026, 3, 1, 23, 59, 0, tzinfo=UTC)),
            _task_metric(completed_at=datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)),
            _task_metric(completed_at=datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)),
        ]
        result = bucket_task_completions(records, start, end, BucketSize.DAY)
        assert len(result) == 1
        assert result[0].value == 1.0


# ── bucket_success_rate tests ──────────────────────────────────


@pytest.mark.unit
class TestBucketSuccessRate:
    """bucket_success_rate pure function."""

    def test_empty_records(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        result = bucket_success_rate([], start, end, BucketSize.DAY)
        assert len(result) == 1
        assert result[0].value == 0.0

    def test_all_success(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(
                completed_at=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), is_success=True
            ),
            _task_metric(
                completed_at=datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC), is_success=True
            ),
        ]
        result = bucket_success_rate(records, start, end, BucketSize.DAY)
        assert result[0].value == 1.0

    def test_all_failure(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(
                completed_at=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
                is_success=False,
            ),
            _task_metric(
                completed_at=datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC),
                is_success=False,
            ),
        ]
        result = bucket_success_rate(records, start, end, BucketSize.DAY)
        assert result[0].value == 0.0

    def test_mixed_success(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(
                completed_at=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), is_success=True
            ),
            _task_metric(
                completed_at=datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC),
                is_success=False,
            ),
            _task_metric(
                completed_at=datetime(2026, 3, 1, 16, 0, 0, tzinfo=UTC), is_success=True
            ),
            _task_metric(
                completed_at=datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC),
                is_success=False,
            ),
        ]
        result = bucket_success_rate(records, start, end, BucketSize.DAY)
        assert len(result) == 2
        # Day 1: 2/3 success
        assert result[0].value == pytest.approx(2.0 / 3.0)
        # Day 2: 0/1 success
        assert result[1].value == 0.0

    def test_empty_bucket_is_zero(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
        records = [
            _task_metric(
                completed_at=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), is_success=True
            ),
        ]
        result = bucket_success_rate(records, start, end, BucketSize.DAY)
        assert result[0].value == 1.0
        assert result[1].value == 0.0  # no tasks = 0.0, not NaN


# ── project_daily_spend tests ──────────────────────────────────


@pytest.mark.unit
class TestProjectDailySpend:
    """project_daily_spend pure function."""

    def test_empty_records(self) -> None:
        result = project_daily_spend([], horizon_days=7)
        assert result.projected_total == 0.0
        assert result.avg_daily_spend == 0.0
        assert result.confidence == 0.0
        assert result.days_until_exhausted is None
        assert len(result.daily_projections) == 7

    def test_known_daily_spend(self) -> None:
        # Records on Mar 1, 2, 3 span 3 inclusive calendar days.  Total
        # 30 over 3 days -> 10/day, projected over 4 days = 40.
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=10.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC), cost=10.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC), cost=10.0
            ),
        ]
        result = project_daily_spend(records, horizon_days=4)
        assert result.avg_daily_spend == pytest.approx(10.0)
        assert result.projected_total == pytest.approx(40.0)
        assert len(result.daily_projections) == 4
        # Cumulative: day1=10, day2=20, day3=30, day4=40
        assert result.daily_projections[0].projected_spend == pytest.approx(10.0)
        assert result.daily_projections[3].projected_spend == pytest.approx(40.0)

    def test_zero_spend(self) -> None:
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=0.0
            ),
        ]
        result = project_daily_spend(records, horizon_days=7)
        assert result.projected_total == 0.0
        assert result.avg_daily_spend == 0.0
        assert result.days_until_exhausted is None

    def test_days_until_exhausted(self) -> None:
        # Inclusive span: Mar 1..Mar 2 = 2 days, total 20 -> 10/day.
        # 50 remaining / 10/day = 5 days exactly.
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
                cost=10.0,
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC),
                cost=10.0,
            ),
        ]
        result = project_daily_spend(
            records,
            horizon_days=14,
            budget_total_monthly=100.0,
            budget_remaining=50.0,
        )
        assert result.avg_daily_spend == pytest.approx(10.0)
        assert result.days_until_exhausted == 5

    def test_no_budget_means_no_exhaustion(self) -> None:
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=10.0
            ),
        ]
        result = project_daily_spend(
            records,
            horizon_days=7,
            budget_total_monthly=0.0,
            budget_remaining=0.0,
        )
        assert result.days_until_exhausted is None

    def test_confidence_full_coverage(self) -> None:
        # Records on all 3 days of a 3-day span -> confidence 1.0
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=1.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC), cost=1.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC), cost=1.0
            ),
        ]
        result = project_daily_spend(records, horizon_days=7)
        assert result.confidence == 1.0

    def test_confidence_partial_coverage(self) -> None:
        # Inclusive span: Mar 1..Mar 4 = 4 days, data on 2 of them -> 0.5
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=1.0
            ),
            _cost_record(
                timestamp=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC), cost=1.0
            ),
        ]
        result = project_daily_spend(records, horizon_days=7)
        assert result.confidence == pytest.approx(0.5)

    def test_forecast_model_is_frozen(self) -> None:
        result = project_daily_spend([], horizon_days=3)
        with pytest.raises(ValidationError):
            result.projected_total = 99.0  # type: ignore[misc]

    def test_projections_have_sequential_dates(self) -> None:
        anchor = datetime(2026, 3, 10, 14, 0, 0, tzinfo=UTC)
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), cost=5.0
            ),
        ]
        result = project_daily_spend(records, horizon_days=5, now=anchor)
        days = [p.day for p in result.daily_projections]
        # First projection is the day after the anchor
        assert days[0] == anchor.date() + timedelta(days=1)
        for i in range(1, len(days)):
            assert days[i] == days[i - 1] + timedelta(days=1)


# ── generate_bucket_starts tests ───────────────────────────────


@pytest.mark.unit
class TestGenerateBucketStarts:
    """generate_bucket_starts public function."""

    def test_empty_when_start_equals_end(self) -> None:
        t = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        assert generate_bucket_starts(t, t, BucketSize.DAY) == []

    def test_daily_bucket_count(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 4, 0, 0, 0, tzinfo=UTC)
        result = generate_bucket_starts(start, end, BucketSize.DAY)
        assert len(result) == 3

    def test_hourly_bucket_count(self) -> None:
        start = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC)
        result = generate_bucket_starts(start, end, BucketSize.HOUR)
        assert len(result) == 4

    def test_start_is_floored_to_bucket(self) -> None:
        start = datetime(2026, 3, 1, 10, 30, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 13, 0, 0, tzinfo=UTC)
        result = generate_bucket_starts(start, end, BucketSize.HOUR)
        assert result[0] == datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    def test_result_is_sorted(self) -> None:
        start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 3, 5, 0, 0, 0, tzinfo=UTC)
        result = generate_bucket_starts(start, end, BucketSize.DAY)
        assert result == sorted(result)


# ── Budget exhaustion edge case ────────────────────────────────


@pytest.mark.unit
class TestBudgetExhaustionEdgeCases:
    """Edge cases for days_until_exhausted."""

    def test_zero_remaining_budget(self) -> None:
        records = [
            _cost_record(
                timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
                cost=10.0,
            ),
        ]
        result = project_daily_spend(
            records,
            horizon_days=7,
            budget_total_monthly=100.0,
            budget_remaining=0.0,
        )
        assert result.days_until_exhausted == 0


# ── Enum value tests ───────────────────────────────────────────


@pytest.mark.unit
class TestEnumValues:
    """Verify enum string values for API serialization."""

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (TrendPeriod.SEVEN_DAYS, "7d"),
            (TrendPeriod.THIRTY_DAYS, "30d"),
            (TrendPeriod.NINETY_DAYS, "90d"),
        ],
    )
    def test_trend_period_values(self, member: TrendPeriod, expected: str) -> None:
        assert member.value == expected

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (TrendMetric.TASKS_COMPLETED, "tasks_completed"),
            (TrendMetric.SPEND, "spend"),
            (TrendMetric.ACTIVE_AGENTS, "active_agents"),
            (TrendMetric.SUCCESS_RATE, "success_rate"),
        ],
    )
    def test_trend_metric_values(self, member: TrendMetric, expected: str) -> None:
        assert member.value == expected

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (BucketSize.HOUR, "hour"),
            (BucketSize.DAY, "day"),
        ],
    )
    def test_bucket_size_values(self, member: BucketSize, expected: str) -> None:
        assert member.value == expected
