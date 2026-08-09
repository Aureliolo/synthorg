"""The plan delete asks about live work and deletes as one operation.

Counting live tasks in one call and deleting in another leaves a window: a
task filed in between is stranded on a plan id that no longer resolves, and
nothing ever reports it. The guard therefore lives in the repository, and the
service must not reach for the unconditional delete beside it.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.services.plan_service import (
    TERMINAL_TASK_STATUS_VALUES,
    PlanService,
)
from synthorg.core.domain_errors import PlanNotDeletableError
from synthorg.core.persistence_errors import RecordNotFoundError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionRepository,
)
from synthorg.persistence.plan_protocol import PlanDeleteOutcome, PlanRepository
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _plan(status: PlanStatus = PlanStatus.EXECUTING) -> Plan:
    return Plan(
        project=NotBlankStr("proj-1"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the loop"),
        parent_task_id=NotBlankStr("task-1"),
        created_at=_NOW,
        updated_at=_NOW,
        items=(
            PlanItem(
                id=NotBlankStr(str(as_uuid("item-1"))),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Build the thing"),
                kind=PlanItemKind.WORK,
                owner=NotBlankStr("engineer"),
                expected_artifacts=(NotBlankStr("the thing"),),
                acceptance_criteria=(NotBlankStr("it works"),),
            ),
        ),
        status=status,
    )


class TestGuardedDelete:
    async def test_the_guard_and_the_delete_are_one_call(self) -> None:
        repo = mock_of[PlanRepository]()
        repo.delete_if_no_live_tasks.return_value = PlanDeleteOutcome(deleted=True)
        service = PlanService(
            repo=repo,
            clock=FakeClock(start=_NOW),
            transitions=mock_of[LifecycleTransitionRepository](),
        )
        plan = _plan()

        await service.delete(plan, requested_by="operator-1")

        repo.delete_if_no_live_tasks.assert_awaited_once()
        # The unconditional delete beside it is exactly the window this fix
        # closes, so reaching for it would reopen it.
        repo.delete.assert_not_awaited()

    async def test_the_declared_terminal_set_reaches_the_repository(self) -> None:
        """The lifecycle lives in the domain; the guard runs as SQL."""
        repo = mock_of[PlanRepository]()
        repo.delete_if_no_live_tasks.return_value = PlanDeleteOutcome(deleted=True)
        service = PlanService(
            repo=repo,
            clock=FakeClock(start=_NOW),
            transitions=mock_of[LifecycleTransitionRepository](),
        )

        await service.delete(_plan(), requested_by="operator-1")

        _, kwargs = repo.delete_if_no_live_tasks.await_args
        assert kwargs["terminal_statuses"] == TERMINAL_TASK_STATUS_VALUES
        assert TaskStatus.COMPLETED.value in kwargs["terminal_statuses"]
        assert TaskStatus.IN_PROGRESS.value not in kwargs["terminal_statuses"]

    async def test_live_work_refuses_and_the_message_counts_it(self) -> None:
        repo = mock_of[PlanRepository]()
        repo.delete_if_no_live_tasks.return_value = PlanDeleteOutcome(
            deleted=False, live_task_count=3
        )
        service = PlanService(
            repo=repo,
            clock=FakeClock(start=_NOW),
            transitions=mock_of[LifecycleTransitionRepository](),
        )

        with pytest.raises(PlanNotDeletableError, match="3 of its items"):
            await service.delete(_plan(), requested_by="operator-1")

    async def test_nothing_deleted_and_nothing_building_is_a_missing_row(self) -> None:
        """The audit line may only follow a delete that found something."""
        repo = mock_of[PlanRepository]()
        repo.delete_if_no_live_tasks.return_value = PlanDeleteOutcome(deleted=False)
        service = PlanService(
            repo=repo,
            clock=FakeClock(start=_NOW),
            transitions=mock_of[LifecycleTransitionRepository](),
        )

        with pytest.raises(RecordNotFoundError):
            await service.delete(_plan(), requested_by="operator-1")

    async def test_a_terminal_plan_is_refused_before_the_repository(self) -> None:
        """It is the record of what was decided; its verdicts outlive it."""
        repo = mock_of[PlanRepository]()
        service = PlanService(
            repo=repo,
            clock=FakeClock(start=_NOW),
            transitions=mock_of[LifecycleTransitionRepository](),
        )

        with pytest.raises(PlanNotDeletableError, match="already decided"):
            await service.delete(
                _plan(status=PlanStatus.COMPLETED), requested_by="operator-1"
            )

        repo.delete_if_no_live_tasks.assert_not_awaited()
