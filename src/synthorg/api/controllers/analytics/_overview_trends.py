# module-kind: code
"""Daily 7-day sparkline series for the analytics overview.

Each builder turns already-fetched raw records into the 7 daily
``TrendDataPoint`` buckets the dashboard's top metric cards render.
The sources are the honest historical records the system actually
keeps: task-metric completions, agent lifecycle events, and approval
requests. No series is synthesised from point-in-time state.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Final

from synthorg.budget.trends import TrendDataPoint
from synthorg.core.approval import ApprovalItem
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord

_WINDOW_DAYS: Final[int] = 7

_ROSTER_ADDS = frozenset({LifecycleEventType.HIRED})
_ROSTER_REMOVES = frozenset({LifecycleEventType.FIRED, LifecycleEventType.OFFBOARDED})


def _day_starts(now: datetime) -> list[datetime]:
    """Midnights for the trailing 7-day window, oldest first.

    Returns:
        Seven day-start datetimes ending with today's midnight.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        today - timedelta(days=offset) for offset in range(_WINDOW_DAYS - 1, -1, -1)
    ]


def _count_per_day(
    timestamps: Sequence[datetime],
    now: datetime,
) -> tuple[TrendDataPoint, ...]:
    """Bucket timestamps into daily counts over the trailing window.

    Returns:
        Seven daily count buckets, oldest first.
    """
    days = _day_starts(now)
    counts = Counter(ts.astimezone(days[0].tzinfo).date() for ts in timestamps)
    return tuple(
        TrendDataPoint(timestamp=day, value=float(counts.get(day.date(), 0)))
        for day in days
    )


def tasks_completed_per_day(
    metrics: Sequence[TaskMetricRecord],
    now: datetime,
) -> tuple[TrendDataPoint, ...]:
    """Daily task-completion counts from performance metric records.

    Returns:
        Seven daily completion-count buckets, oldest first.
    """
    return _count_per_day([m.completed_at for m in metrics], now)


def approvals_raised_per_day(
    items: Sequence[ApprovalItem],
    now: datetime,
) -> tuple[TrendDataPoint, ...]:
    """Daily approval-request counts within the trailing window.

    Returns:
        Seven daily approval-count buckets, oldest first.
    """
    cutoff = _day_starts(now)[0]
    return _count_per_day(
        [item.created_at for item in items if item.created_at >= cutoff],
        now,
    )


def roster_size_per_day(
    current_total: int,
    events: Sequence[AgentLifecycleEvent],
    now: datetime,
) -> tuple[TrendDataPoint, ...]:
    """Daily roster size reconstructed backwards from the live total.

    The roster at the end of an earlier day equals today's total minus
    the net hires that happened after that day, so the series stays
    consistent with the live agent count on the same card.

    Returns:
        Seven daily roster-size buckets, oldest first.
    """
    days = _day_starts(now)
    net_by_day: Counter[date] = Counter()
    for event in events:
        day = event.timestamp.astimezone(days[0].tzinfo).date()
        if event.event_type in _ROSTER_ADDS:
            net_by_day[day] += 1
        elif event.event_type in _ROSTER_REMOVES:
            net_by_day[day] -= 1

    sizes: list[int] = [0] * _WINDOW_DAYS
    running = current_total
    for index in range(_WINDOW_DAYS - 1, -1, -1):
        sizes[index] = max(running, 0)
        running -= net_by_day.get(days[index].date(), 0)
    return tuple(
        TrendDataPoint(timestamp=day, value=float(size))
        for day, size in zip(days, sizes, strict=True)
    )
