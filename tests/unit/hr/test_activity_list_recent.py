"""Tests for ActivityFeedService.list_recent_activity().

The new entry point is the no-agent-required variant of
``get_agent_activity``. It powers ``synthorg_activities_list``: a feed
that operators query for "recent activity in this project" or
"recent events on this task" without scoping to a single agent.
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.tracker import CostTracker
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.activity_service import ActivityFeedService
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.persistence_protocol import LifecycleEventRepository
from tests._shared import mock_of


def _now() -> datetime:
    return datetime.now(UTC)


def _make_task_metric(
    *,
    agent_id: str,
    task_id: str,
    completed_at: datetime,
    is_success: bool = True,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id),
        task_type=TaskType.REVIEW,
        completed_at=completed_at,
        is_success=is_success,
        duration_seconds=60.0,
        cost=0.5,
        currency=DEFAULT_CURRENCY,
        turns_used=2,
        tokens_used=100,
        complexity=Complexity.MEDIUM,
    )


@pytest.fixture
def lifecycle_repo() -> LifecycleEventRepository:
    repo: LifecycleEventRepository = mock_of[LifecycleEventRepository](
        list_events=AsyncMock(return_value=()),
    )
    return repo


@pytest.fixture
def performance_tracker() -> PerformanceTracker:
    tracker: PerformanceTracker = mock_of[PerformanceTracker](
        get_task_metrics=MagicMock(return_value=()),
    )
    return tracker


@pytest.fixture
def service(
    lifecycle_repo: LifecycleEventRepository,
    performance_tracker: PerformanceTracker,
) -> ActivityFeedService:
    return ActivityFeedService(
        performance_tracker=performance_tracker,
        lifecycle_repo=lifecycle_repo,
    )


class TestListRecentActivity:
    """list_recent_activity merges sources with optional filters."""

    @pytest.mark.unit
    async def test_unfiltered_happy_path(
        self,
        service: ActivityFeedService,
        performance_tracker: PerformanceTracker,
    ) -> None:
        completed_at = _now() - timedelta(minutes=5)
        cast(MagicMock, performance_tracker.get_task_metrics).return_value = (
            _make_task_metric(
                agent_id="agent-1",
                task_id="task-1",
                completed_at=completed_at,
            ),
            _make_task_metric(
                agent_id="agent-2",
                task_id="task-2",
                completed_at=completed_at,
            ),
        )

        page, total = await service.list_recent_activity(offset=0, limit=10)
        # Two completed task metrics produce two events (no started_at).
        assert total == 2
        assert len(page) == 2
        assert all("task_id" in event.related_ids for event in page)

    @pytest.mark.unit
    async def test_task_id_filter(
        self,
        service: ActivityFeedService,
        performance_tracker: PerformanceTracker,
    ) -> None:
        completed_at = _now() - timedelta(minutes=5)
        cast(MagicMock, performance_tracker.get_task_metrics).return_value = (
            _make_task_metric(
                agent_id="agent-1",
                task_id="task-1",
                completed_at=completed_at,
            ),
            _make_task_metric(
                agent_id="agent-2",
                task_id="task-2",
                completed_at=completed_at,
            ),
            _make_task_metric(
                agent_id="agent-3",
                task_id="task-1",
                completed_at=completed_at - timedelta(seconds=10),
            ),
        )
        page, total = await service.list_recent_activity(
            task_id="task-1",
            offset=0,
            limit=10,
        )
        assert total == 2
        for event in page:
            assert event.related_ids.get("task_id") == "task-1"

    @pytest.mark.unit
    async def test_project_filter_excludes_unattributed_sources(
        self,
        lifecycle_repo: LifecycleEventRepository,
        performance_tracker: PerformanceTracker,
    ) -> None:
        """``project=...`` keeps only events that carry the matching project_id.

        Pins the new contract: ``TaskMetricRecord`` carries no project
        attribution, so when a project filter is set those metrics
        must be dropped entirely (otherwise the feed would leak
        cross-project activity through the merge). Cost records are
        filtered in place by ``project_id``.
        """
        from synthorg.budget.cost_record import CostRecord

        completed_at = _now() - timedelta(minutes=5)
        # One task metric per project would survive a project filter
        # before the regression fix; assert it is now dropped.
        cast(MagicMock, performance_tracker.get_task_metrics).return_value = (
            _make_task_metric(
                agent_id="agent-x",
                task_id="task-x",
                completed_at=completed_at,
            ),
        )

        def _cost(*, task_id: str, project_id: str, cost: float) -> CostRecord:
            return CostRecord(
                agent_id=NotBlankStr("agent-x"),
                task_id=NotBlankStr(task_id),
                project_id=NotBlankStr(project_id),
                provider=NotBlankStr("test-provider"),
                model=NotBlankStr("test-small-001"),
                input_tokens=10,
                output_tokens=5,
                cost=cost,
                currency=DEFAULT_CURRENCY,
                timestamp=completed_at,
            )

        cost_in_scope = _cost(task_id="task-1", project_id="proj-1", cost=1.0)
        cost_out_of_scope = _cost(
            task_id="task-2",
            project_id="proj-other",
            cost=2.0,
        )
        cost_tracker = mock_of[CostTracker](
            get_records=AsyncMock(return_value=(cost_in_scope, cost_out_of_scope)),
        )
        service = ActivityFeedService(
            performance_tracker=performance_tracker,
            lifecycle_repo=lifecycle_repo,
            cost_tracker=cost_tracker,
        )

        page, total = await service.list_recent_activity(
            project="proj-1",
            offset=0,
            limit=10,
        )
        # Only the in-scope cost record survives. Task metrics are
        # dropped because they have no project attribution; the
        # out-of-scope cost record is filtered out. We verify by
        # related task_id since the activity-event projection does not
        # surface project_id directly.
        assert total == 1
        assert len(page) == 1
        assert page[0].related_ids.get("task_id") == "task-1"

    @pytest.mark.unit
    async def test_pagination(
        self,
        service: ActivityFeedService,
        performance_tracker: PerformanceTracker,
    ) -> None:
        completed_at = _now() - timedelta(minutes=5)
        cast(MagicMock, performance_tracker.get_task_metrics).return_value = tuple(
            _make_task_metric(
                agent_id=f"agent-{i}",
                task_id=f"task-{i}",
                completed_at=completed_at - timedelta(seconds=i),
            )
            for i in range(5)
        )
        page, total = await service.list_recent_activity(offset=2, limit=2)
        assert total == 5
        assert len(page) == 2

    @pytest.mark.unit
    async def test_invalid_offset_rejected(
        self,
        service: ActivityFeedService,
    ) -> None:
        with pytest.raises(ValueError, match="offset"):
            await service.list_recent_activity(offset=-1, limit=10)

    @pytest.mark.unit
    async def test_invalid_window_rejected(
        self,
        service: ActivityFeedService,
    ) -> None:
        with pytest.raises(ValueError, match="window_hours"):
            await service.list_recent_activity(
                offset=0,
                limit=10,
                window_hours=10_000,
            )

    @pytest.mark.unit
    async def test_isolated_source_failure_does_not_abort(
        self,
        lifecycle_repo: LifecycleEventRepository,
        performance_tracker: PerformanceTracker,
    ) -> None:
        """A failing optional source must not abort the merge."""

        class _BoomCostTracker:
            async def get_records(
                self,
                **_: object,
            ) -> tuple[CostRecord, ...]:
                msg = "cost tracker explodes"
                raise RuntimeError(msg)

        completed_at = _now() - timedelta(minutes=5)
        cast(MagicMock, performance_tracker.get_task_metrics).return_value = (
            _make_task_metric(
                agent_id="agent-1",
                task_id="task-1",
                completed_at=completed_at,
            ),
        )
        service = ActivityFeedService(
            performance_tracker=performance_tracker,
            lifecycle_repo=lifecycle_repo,
            cost_tracker=_BoomCostTracker(),  # type: ignore[arg-type]
        )

        page, total = await service.list_recent_activity(offset=0, limit=10)
        # Cost source failed but task metric still produced an event.
        assert total == 1
        assert len(page) == 1
