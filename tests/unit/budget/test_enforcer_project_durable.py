"""Unit tests for BudgetEnforcer with durable project cost aggregate."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import ProjectBudgetExhaustedError
from synthorg.budget.project_cost_aggregate import ProjectCostAggregate
from synthorg.budget.tracker import CostTracker
from synthorg.core.enums import Priority, TaskType
from synthorg.core.task import Task

from .conftest import make_cost_record


def _make_task() -> Task:
    return Task(
        id="t-1",
        title="Test task",
        description="A test task",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="alice",
    )


def _make_aggregate(
    project_id: str = "proj-1",
    total_cost: float = 0.0,
    *,
    currency: str = "USD",
) -> ProjectCostAggregate:
    return ProjectCostAggregate(
        project_id=project_id,
        total_cost=total_cost,
        currency=currency,
        total_input_tokens=0,
        total_output_tokens=0,
        record_count=1,
        last_updated=datetime.now(UTC),
    )


def _make_repo(
    get_return: ProjectCostAggregate | None = None,
) -> AsyncMock:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=get_return)
    return repo


def _make_enforcer(
    *,
    tracker: CostTracker | None = None,
    project_cost_repo: AsyncMock | None = None,
) -> BudgetEnforcer:
    config = BudgetConfig(total_monthly=100.0)
    t = tracker or CostTracker(budget_config=config)
    return BudgetEnforcer(
        budget_config=config,
        cost_tracker=t,
        project_cost_repo=project_cost_repo,
    )


@pytest.mark.unit
class TestCheckProjectBudgetDurable:
    """Tests for check_project_budget() with durable aggregate."""

    async def test_uses_aggregate_when_available(self) -> None:
        repo = _make_repo(_make_aggregate(total_cost=8.0))
        enforcer = _make_enforcer(project_cost_repo=repo)

        # Should pass: 8.0 < 10.0
        await enforcer.check_project_budget("proj-1", project_budget=10.0)

        repo.get.assert_awaited_once_with("proj-1")

    async def test_raises_from_aggregate_data(self) -> None:
        repo = _make_repo(_make_aggregate(total_cost=15.0))
        enforcer = _make_enforcer(project_cost_repo=repo)

        with pytest.raises(ProjectBudgetExhaustedError) as exc_info:
            await enforcer.check_project_budget("proj-1", project_budget=10.0)

        assert exc_info.value.project_spent >= 15.0

    async def test_falls_back_to_in_memory_on_repo_error(self) -> None:
        repo = _make_repo()
        repo.get.side_effect = RuntimeError("DB error")

        tracker = CostTracker()
        await tracker.record(make_cost_record(project_id="proj-1", cost=2.0))
        enforcer = _make_enforcer(tracker=tracker, project_cost_repo=repo)

        # Falls back to in-memory (2.0 < 10.0), should pass
        await enforcer.check_project_budget("proj-1", project_budget=10.0)

    async def test_uses_in_memory_when_no_repo(self) -> None:
        tracker = CostTracker()
        await tracker.record(make_cost_record(project_id="proj-1", cost=5.0))
        enforcer = _make_enforcer(tracker=tracker)

        # No repo -- uses in-memory tracker
        with pytest.raises(ProjectBudgetExhaustedError) as exc_info:
            await enforcer.check_project_budget("proj-1", project_budget=5.0)

        assert exc_info.value.project_id == "proj-1"
        assert exc_info.value.project_spent >= 5.0
        assert exc_info.value.project_budget == 5.0

    async def test_aggregate_none_treated_as_zero(self) -> None:
        repo = _make_repo(get_return=None)
        enforcer = _make_enforcer(project_cost_repo=repo)

        # No aggregate record -> 0.0 cost, passes any budget
        await enforcer.check_project_budget("proj-1", project_budget=10.0)

    async def test_raises_at_exact_boundary(self) -> None:
        repo = _make_repo(_make_aggregate(total_cost=10.0))
        enforcer = _make_enforcer(project_cost_repo=repo)

        with pytest.raises(ProjectBudgetExhaustedError):
            await enforcer.check_project_budget("proj-1", project_budget=10.0)

    async def test_zero_budget_skips_regardless_of_repo(self) -> None:
        repo = _make_repo(_make_aggregate(total_cost=999.0))
        enforcer = _make_enforcer(project_cost_repo=repo)

        # Zero budget means no enforcement
        await enforcer.check_project_budget("proj-1", project_budget=0.0)
        repo.get.assert_not_awaited()


@pytest.mark.unit
class TestMakeBudgetCheckerDurable:
    """Tests for make_budget_checker() with durable aggregate baseline."""

    async def test_uses_aggregate_baseline(self) -> None:
        repo = _make_repo(_make_aggregate(total_cost=7.0))
        enforcer = _make_enforcer(project_cost_repo=repo)

        task = _make_task()

        checker = await enforcer.make_budget_checker(
            task,
            "alice",
            project_id="proj-1",
            project_budget=10.0,
        )

        assert checker is not None
        repo.get.assert_awaited_once_with("proj-1")

    async def test_falls_back_to_in_memory_on_error(self) -> None:
        repo = _make_repo()
        repo.get.side_effect = RuntimeError("DB error")

        tracker = CostTracker()
        await tracker.record(make_cost_record(project_id="proj-1", cost=3.0))
        enforcer = _make_enforcer(tracker=tracker, project_cost_repo=repo)

        task = _make_task()

        # Should not raise -- falls back to in-memory baseline
        checker = await enforcer.make_budget_checker(
            task,
            "alice",
            project_id="proj-1",
            project_budget=10.0,
        )
        assert checker is not None

    async def test_both_sources_fail_uses_zero_baseline(self) -> None:
        repo = _make_repo()
        repo.get.side_effect = RuntimeError("DB error")

        tracker = CostTracker()
        enforcer = _make_enforcer(tracker=tracker, project_cost_repo=repo)

        task = _make_task()

        # Both repo and in-memory fail for a fresh tracker with
        # no records -- _get_project_cost returns None, baseline
        # defaults to 0.0, checker is still created.
        checker = await enforcer.make_budget_checker(
            task,
            "alice",
            project_id="proj-1",
            project_budget=10.0,
        )
        assert checker is not None
