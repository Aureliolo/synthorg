"""Time-series trend bucketing and budget forecast projections.

Provides pure functions to bucket cost records and task metrics into
time-series data points, and to project future budget spend from
historical data. Follows the same pure-function pattern as
:mod:`~synthorg.budget.category_analytics`.
"""

import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.budget._tracker_helpers import _assert_single_currency
from synthorg.constants import BUDGET_ROUNDING_PRECISION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.budget.cost_record import CostRecord
    from synthorg.hr.performance.models import TaskMetricRecord

# ── Enums ──────────────────────────────────────────────────────


class TrendPeriod(StrEnum):
    """Supported trend lookback periods."""

    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    NINETY_DAYS = "90d"


class TrendMetric(StrEnum):
    """Supported trend metric types."""

    TASKS_COMPLETED = "tasks_completed"
    SPEND = "spend"
    ACTIVE_AGENTS = "active_agents"
    SUCCESS_RATE = "success_rate"


class BucketSize(StrEnum):
    """Time bucket granularity."""

    HOUR = "hour"
    DAY = "day"


# ── Models ─────────────────────────────────────────────────────


class TrendDataPoint(BaseModel):
    """Single data point in a time-series trend.

    Attributes:
        timestamp: Bucket start time (UTC).
        value: Metric value for this bucket.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    timestamp: AwareDatetime = Field(description="Bucket start time (UTC)")
    value: float = Field(description="Metric value for this bucket")


class ForecastPoint(BaseModel):
    """Single day in a budget forecast projection.

    Attributes:
        day: Calendar date.
        projected_spend: Projected cumulative spend for this day
            in the configured currency.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    day: date = Field(description="Calendar date")
    projected_spend: float = Field(
        ge=0.0,
        description="Projected cumulative spend in the configured currency",
    )


class BudgetForecast(BaseModel):
    """Budget spend projection over a time horizon.

    Attributes:
        projected_total: Projected total spend at end of horizon.
        daily_projections: Per-day cumulative spend projections.
        days_until_exhausted: Days until budget exhaustion (None if
            no budget set or zero daily spend). Uses ceiling
            rounding -- the budget is exhausted on or before
            this many days.
        confidence: Confidence score (0.0-1.0) based on data density.
        avg_daily_spend: Average daily spend used for projection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    projected_total: float = Field(
        ge=0.0,
        description=(
            "Projected total spend at end of horizon in the configured "
            "currency (see ``budget.currency``)"
        ),
    )
    daily_projections: tuple[ForecastPoint, ...] = Field(
        description="Per-day cumulative spend projections",
    )
    days_until_exhausted: int | None = Field(
        default=None,
        ge=0,
        description="Days until budget exhaustion",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score based on data density",
    )
    avg_daily_spend: float = Field(
        ge=0.0,
        description=(
            "Average daily spend in the configured currency used for "
            "projection (see ``budget.currency``)"
        ),
    )


# ── Resolver helpers ───────────────────────────────────────────

_PERIOD_TIMEDELTA: dict[TrendPeriod, timedelta] = {
    TrendPeriod.SEVEN_DAYS: timedelta(days=7),
    TrendPeriod.THIRTY_DAYS: timedelta(days=30),
    TrendPeriod.NINETY_DAYS: timedelta(days=90),
}


def resolve_bucket_size(period: TrendPeriod) -> BucketSize:
    """Determine bucket granularity from period.

    Args:
        period: Lookback period.

    Returns:
        HOUR for 7d, DAY for 30d/90d.
    """
    if period == TrendPeriod.SEVEN_DAYS:
        return BucketSize.HOUR
    return BucketSize.DAY


def period_to_timedelta(period: TrendPeriod) -> timedelta:
    """Convert a trend period to a timedelta.

    Args:
        period: Lookback period.

    Returns:
        Equivalent timedelta.
    """
    return _PERIOD_TIMEDELTA[period]


# ── Bucketing helpers ──────────────────────────────────────────


def _bucket_key(ts: datetime, bucket_size: BucketSize) -> datetime:
    """Truncate a timestamp to its bucket start.

    Args:
        ts: Timestamp to truncate.
        bucket_size: Granularity.

    Returns:
        Bucket start datetime (UTC-aware).
    """
    if bucket_size == BucketSize.HOUR:
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def generate_bucket_starts(
    start: datetime,
    end: datetime,
    bucket_size: BucketSize,
) -> list[datetime]:
    """Generate all bucket start times covering [start, end).

    ``start`` is aligned (floored) to the nearest bucket boundary
    before iteration, so the first bucket may begin before ``start``.

    Args:
        start: Period start (floored to bucket boundary).
        end: Period end (exclusive).
        bucket_size: Granularity.

    Returns:
        Sorted list of bucket start datetimes.
    """
    step = timedelta(hours=1) if bucket_size == BucketSize.HOUR else timedelta(days=1)
    current = _bucket_key(start, bucket_size)
    buckets: list[datetime] = []
    while current < end:
        buckets.append(current)
        current = current + step
    return buckets


# ── Pure bucketing functions ───────────────────────────────────


def bucket_cost_records(
    records: Sequence[CostRecord],
    start: datetime,
    end: datetime,
    bucket_size: BucketSize,
) -> tuple[TrendDataPoint, ...]:
    """Group cost records by time bucket and sum cost.

    Empty buckets are filled with 0.0 to produce a continuous
    time series suitable for sparkline rendering.

    Args:
        records: Cost records to bucket.
        start: Period start (inclusive).
        end: Period end (exclusive).
        bucket_size: Granularity (hour or day).

    Returns:
        Sorted tuple of data points, one per bucket.

    Raises:
        MixedCurrencyAggregationError: If the records remaining after
            filtering to ``[start, end)`` span multiple currencies.
            Summing across currencies would produce a meaningless
            monetary total. Records outside the window are not part
            of the aggregation and are not validated.
    """
    bucket_starts = generate_bucket_starts(start, end, bucket_size)
    # Filter to the requested ``[start, end)`` window before
    # validating currency uniformity. Validating the raw input would
    # reject otherwise-valid partial-range queries when the caller
    # passes a multi-currency dataset and asks for a single-currency
    # slice -- the rows outside the window do not contribute to the
    # aggregation, so they are not part of the "is this bucket
    # meaningful?" question.
    in_window_records = tuple(
        record for record in records if start <= record.timestamp < end
    )
    _assert_single_currency(in_window_records)
    sums: dict[datetime, list[float]] = defaultdict(list)

    for record in in_window_records:
        key = _bucket_key(record.timestamp, bucket_size)
        sums[key].append(record.cost)

    return tuple(
        TrendDataPoint(
            timestamp=bucket_start,
            value=round(
                math.fsum(sums.get(bucket_start, [])),
                BUDGET_ROUNDING_PRECISION,
            ),
        )
        for bucket_start in bucket_starts
    )


def bucket_task_completions(
    records: Sequence[TaskMetricRecord],
    start: datetime,
    end: datetime,
    bucket_size: BucketSize,
) -> tuple[TrendDataPoint, ...]:
    """Count completed tasks per time bucket.

    Args:
        records: Task metric records to bucket.
        start: Period start (inclusive).
        end: Period end (exclusive).
        bucket_size: Granularity (hour or day).

    Returns:
        Sorted tuple of data points with task counts.
    """
    bucket_starts = generate_bucket_starts(start, end, bucket_size)
    counts: dict[datetime, int] = defaultdict(int)

    for record in records:
        ts = record.completed_at
        if ts < start or ts >= end:
            continue
        key = _bucket_key(ts, bucket_size)
        counts[key] += 1

    return tuple(
        TrendDataPoint(
            timestamp=bucket_start,
            value=float(counts.get(bucket_start, 0)),
        )
        for bucket_start in bucket_starts
    )


def bucket_success_rate(
    records: Sequence[TaskMetricRecord],
    start: datetime,
    end: datetime,
    bucket_size: BucketSize,
) -> tuple[TrendDataPoint, ...]:
    """Compute success rate (0.0-1.0) per time bucket.

    Buckets with no tasks have a rate of 0.0.

    Args:
        records: Task metric records to bucket.
        start: Period start (inclusive).
        end: Period end (exclusive).
        bucket_size: Granularity (hour or day).

    Returns:
        Sorted tuple of data points with success rates.
    """
    bucket_starts = generate_bucket_starts(start, end, bucket_size)
    totals: dict[datetime, int] = defaultdict(int)
    successes: dict[datetime, int] = defaultdict(int)

    for record in records:
        ts = record.completed_at
        if ts < start or ts >= end:
            continue
        key = _bucket_key(ts, bucket_size)
        totals[key] += 1
        if record.is_success:
            successes[key] += 1

    return tuple(
        TrendDataPoint(
            timestamp=bucket_start,
            value=(
                round(
                    successes.get(bucket_start, 0) / totals[bucket_start],
                    BUDGET_ROUNDING_PRECISION,
                )
                if totals.get(bucket_start, 0) > 0
                else 0.0
            ),
        )
        for bucket_start in bucket_starts
    )


# ── Forecast ───────────────────────────────────────────────────


def _compute_daily_spend(
    records: Sequence[CostRecord],
) -> tuple[float, float, int]:
    """Compute average daily spend and confidence inputs.

    Caller must ensure ``records`` is non-empty; an empty
    sequence raises ``IndexError``.

    Args:
        records: Non-empty cost records.

    Returns:
        Tuple of (avg_daily_spend, confidence, lookback_days).
    """
    timestamps = sorted(r.timestamp for r in records)
    # Inclusive span: a record set spanning calendar days D0..DN covers N+1
    # days, not N. Without +1 a one-day window divides total_cost by 0 (then
    # clamped to 1) and inflates avg_daily by a factor of 2 for two-day spans.
    lookback_days = max(
        (timestamps[-1].date() - timestamps[0].date()).days + 1,
        1,
    )
    total_cost = round(
        math.fsum(r.cost for r in records),
        BUDGET_ROUNDING_PRECISION,
    )
    avg_daily = round(total_cost / lookback_days, BUDGET_ROUNDING_PRECISION)
    days_with_data = len({r.timestamp.date() for r in records})
    confidence = round(
        min(days_with_data / lookback_days, 1.0),
        BUDGET_ROUNDING_PRECISION,
    )
    return avg_daily, confidence, lookback_days


def _build_projections(
    avg_daily: float,
    horizon_days: int,
    today: date,
) -> tuple[ForecastPoint, ...]:
    """Build cumulative daily projection points.

    Args:
        avg_daily: Average daily spend in the configured currency.
        horizon_days: Number of days to project.
        today: Reference date for projection start.

    Returns:
        Tuple of forecast points, one per projected day.
    """
    return tuple(
        ForecastPoint(
            day=today + timedelta(days=i + 1),
            projected_spend=round(
                avg_daily * (i + 1),
                BUDGET_ROUNDING_PRECISION,
            ),
        )
        for i in range(horizon_days)
    )


def project_daily_spend(
    records: Sequence[CostRecord],
    *,
    horizon_days: int,
    budget_total_monthly: float = 0.0,
    budget_remaining: float | None = None,
    now: datetime | None = None,
) -> BudgetForecast:
    """Project future budget spend using average daily spend.

    Computes the average daily spend from the provided records,
    then projects forward for ``horizon_days``. Confidence is
    derived from data density: days with at least one record
    divided by the total lookback span in days (clamped 0.0-1.0).

    Args:
        records: Historical cost records for the lookback period.
        horizon_days: Number of days to project forward.
        budget_total_monthly: Monthly budget total (0.0 if unset).
        budget_remaining: Remaining budget in the configured currency,
            or ``None`` if unknown. ``None`` means ``days_until_exhausted``
            cannot be computed; it is distinct from ``0.0`` (actually
            exhausted) so callers who omit the kwarg do not accidentally
            advertise zero-day runway.
        now: Reference time (defaults to current UTC time).

    Returns:
        Budget forecast with daily projections.

    Raises:
        MixedCurrencyAggregationError: If *records* span multiple
            currencies. The forecast would conflate currencies on the
            avg-daily-spend computation and return a meaningless
            projection; raising at the boundary surfaces the bug at
            the call site.
    """
    today = (now or datetime.now(UTC)).date()

    if not records:
        return BudgetForecast(
            projected_total=0.0,
            daily_projections=_build_projections(0.0, horizon_days, today),
            days_until_exhausted=None,
            confidence=0.0,
            avg_daily_spend=0.0,
        )

    _assert_single_currency(records)
    avg_daily, confidence, _ = _compute_daily_spend(records)
    projections = _build_projections(avg_daily, horizon_days, today)
    projected_total = round(
        avg_daily * horizon_days,
        BUDGET_ROUNDING_PRECISION,
    )

    days_until: int | None = None
    if budget_total_monthly > 0 and budget_remaining is not None and avg_daily > 0:
        days_until = max(math.ceil(budget_remaining / avg_daily), 0)

    return BudgetForecast(
        projected_total=projected_total,
        daily_projections=projections,
        days_until_exhausted=days_until,
        confidence=confidence,
        avg_daily_spend=avg_daily,
    )
