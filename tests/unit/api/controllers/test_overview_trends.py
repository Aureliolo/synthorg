"""Daily 7-day sparkline series builders for the analytics overview."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.api.controllers.analytics._overview_trends import (
    approvals_raised_per_day,
    roster_size_per_day,
    tasks_completed_per_day,
)
from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.approval import ApprovalItem
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from tests._shared import sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 1, 15, 30, tzinfo=UTC)


def _metric(completed_at: datetime) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=sid("agent-a"),
        task_id=sid("task-1"),
        task_type=TaskType.RESEARCH,
        completed_at=completed_at,
        is_success=True,
        duration_seconds=10.0,
        cost=0.1,
        currency="USD",
        turns_used=1,
        tokens_used=100,
        complexity=Complexity.MEDIUM,
    )


def _lifecycle(
    event_type: LifecycleEventType,
    timestamp: datetime,
) -> AgentLifecycleEvent:
    return AgentLifecycleEvent(
        agent_id=sid("agent-a"),
        agent_name="Agent A",
        event_type=event_type,
        timestamp=timestamp,
        initiated_by="ceo",
        details="",
    )


def _approval(created_at: datetime) -> ApprovalItem:
    return ApprovalItem(
        action_type=sid("budget_change"),
        title="Raise budget",
        description="raise budget",
        risk_level=ApprovalRiskLevel.MEDIUM,
        requested_by=sid("agent-a"),
        created_at=created_at,
    )


class TestTasksCompletedPerDay:
    def test_buckets_by_day_and_pads_empty_days(self) -> None:
        metrics = [
            _metric(_NOW - timedelta(hours=1)),
            _metric(_NOW - timedelta(hours=2)),
            _metric(_NOW - timedelta(days=2)),
        ]
        series = tasks_completed_per_day(metrics, _NOW)
        assert len(series) == 7
        assert [p.value for p in series] == [0, 0, 0, 0, 1, 0, 2]

    def test_empty_metrics_yield_all_zero(self) -> None:
        series = tasks_completed_per_day([], _NOW)
        assert [p.value for p in series] == [0] * 7


class TestApprovalsRaisedPerDay:
    def test_counts_only_window_items(self) -> None:
        items = [
            _approval(_NOW - timedelta(hours=3)),
            _approval(_NOW - timedelta(days=30)),
        ]
        series = approvals_raised_per_day(items, _NOW)
        assert [p.value for p in series] == [0, 0, 0, 0, 0, 0, 1]


class TestRosterSizePerDay:
    def test_walks_back_from_current_total(self) -> None:
        events = [
            _lifecycle(LifecycleEventType.HIRED, _NOW - timedelta(days=1)),
            _lifecycle(LifecycleEventType.FIRED, _NOW - timedelta(days=3)),
        ]
        series = roster_size_per_day(5, events, _NOW)
        # End-of-day sizes, oldest first: 5 before the fire (3 days
        # ago), 4 from the fire until the hire (yesterday), 5 after.
        assert [p.value for p in series] == [5, 5, 5, 4, 4, 5, 5]

    def test_no_events_is_flat(self) -> None:
        series = roster_size_per_day(3, [], _NOW)
        assert [p.value for p in series] == [3] * 7
