"""Tests for re-planning a dispatched initiative."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._plan_replan import RevisionInputs, replan_initiative
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_engine import TaskEngine
from synthorg.persistence.plan_protocol import PlanFilterSpec
from tests._shared import as_uuid, mock_of, sid
from tests._shared.app_state import make_app_state
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

# A configured mock_of double: typed at the boundary, but assertion helpers
# (assert_awaited, await_args_list) are not on the Protocol it satisfies.
_Configured = Any  # type: ignore[explicit-any]

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_ITEM_A = sid("item-a")
_ITEM_B = sid("item-b")


def _item(item_id: str, title: str) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(title),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
    )


def _plan(status: PlanStatus) -> Plan:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=(_item(_ITEM_A, "Original A"),),
        status=status,
        created_at=now,
        updated_at=now,
    )


def _task(item_id: str, status: TaskStatus) -> Task:
    # A CREATED task carries no assignee; anything past it does.
    assigned = None if status is TaskStatus.CREATED else sid("agent-1")
    return Task(
        id=as_uuid(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=as_uuid(item_id),
        created_by="manager",
        assigned_to=assigned,
        status=status,
    )


async def _seed(
    status: PlanStatus = PlanStatus.EXECUTING,
    *tasks: Task,
) -> tuple[AppState, FakePersistenceBackend, _Configured]:
    backend = FakePersistenceBackend()
    await backend.connect()
    await backend.plans.save(_plan(status))
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(_PLAN_ID),
            status=ProjectStatus.ACTIVE,
        )
    )
    for task in tasks:
        await backend.tasks.save(task)

    async def _get_task(task_id: str) -> Task | None:
        # Mirror the real engine's read-through so terminate_task sees the
        # live persisted status rather than the caller's snapshot.
        return await backend.tasks.get(NotBlankStr(task_id))

    engine = mock_of[TaskEngine](
        transition_task=AsyncMock(return_value=(None, None)),
        get_task=AsyncMock(side_effect=_get_task),
    )
    state = make_app_state(persistence=backend, task_engine=engine)
    return state, backend, engine


_REVISION = RevisionInputs(items=(_item(_ITEM_B, "Revised B"),))


class TestReplan:
    async def test_retires_the_current_plan_and_opens_a_successor(self) -> None:
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        successor = await replan_initiative(
            state,
            existing,
            revision=_REVISION,
            requested_by="admin",
        )

        assert successor.id != existing.id
        assert successor.status is PlanStatus.PENDING_REVIEW
        assert [item.title for item in successor.items] == ["Revised B"]
        retired = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert retired is not None
        assert retired.status is PlanStatus.SUPERSEDED

    async def test_the_project_repoints_at_the_successor(self) -> None:
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        successor = await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.plan_id == successor.id
        # The initiative is live throughout, it is only being re-scoped.
        assert project.status is ProjectStatus.ACTIVE

    async def test_the_successor_carries_the_objective_forward(self) -> None:
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        successor = await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        assert successor.project == existing.project
        assert successor.objective_id == existing.objective_id
        assert successor.objective_title == existing.objective_title
        # The retired items stay diffable against the revision.
        assert successor.version_history[-1].items == existing.items

    async def test_in_flight_work_from_the_retired_plan_is_cancelled(self) -> None:
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        engine.transition_task.assert_awaited()
        targets = [call.args[1] for call in engine.transition_task.await_args_list]
        assert TaskStatus.CANCELLED in targets

    async def test_already_finished_work_is_left_alone(self) -> None:
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        engine.transition_task.assert_not_called()

    async def test_a_created_task_is_rejected_not_cancelled(self) -> None:
        """The lifecycle forbids CREATED -> CANCELLED; it is rejected."""
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.CREATED),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        targets = [call.args[1] for call in engine.transition_task.await_args_list]
        assert targets == [TaskStatus.REJECTED]

    async def test_work_finished_during_the_drain_is_skipped(self) -> None:
        """A task terminal in persistence is not driven through a dead transition.

        The teardown snapshots every task before terminating any; a task can
        finish in the interim. terminate_task re-reads live state, so the stale
        IN_PROGRESS snapshot must not force an invalid transition on the row
        that has since completed.
        """
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
        )
        # The drain snapshots IN_PROGRESS; the read-through inside
        # terminate_task sees the row after it completed mid-drain.
        engine.get_task = AsyncMock(return_value=_task(_ITEM_A, TaskStatus.COMPLETED))
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        engine.transition_task.assert_not_called()

    async def test_only_one_live_plan_remains_for_the_project(self) -> None:
        """The invariant the ordering exists to protect."""
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        plans = await backend.plans.query(
            PlanFilterSpec(project=NotBlankStr(sid(_PROJECT))), limit=50
        )
        live = [p for p in plans if p.status is not PlanStatus.SUPERSEDED]
        assert len(live) == 1

    @pytest.mark.parametrize(
        "status",
        [
            PlanStatus.DRAFT,
            PlanStatus.PENDING_REVIEW,
            PlanStatus.COMPLETED,
            PlanStatus.REJECTED,
        ],
    )
    async def test_a_plan_that_is_not_dispatched_is_refused(
        self, status: PlanStatus
    ) -> None:
        """A plan under review is edited in place; a terminal one is done."""
        state, backend, _ = await _seed(status)
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        with pytest.raises(ConflictError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        untouched = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert untouched is not None
        assert untouched.status is status
