"""Initiative rollup driven through the real ``TaskEngine`` observer seam.

The unit tests call ``recompute`` directly. This exercises the wiring the
product actually runs on: the service registered as a ``TaskEngine`` observer,
a task transitioned through the engine's own mutation path, and the plan and
project advancing off the resulting event.

The negative case is the one that matters: a task that has executed but is
still awaiting verification must not complete the initiative.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from synthorg.api.services.plan_service import PlanService
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from tests._shared import FakeClock, as_uuid, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend
from tests.unit.engine.task_engine_helpers import (
    FakeMessageBus as EngineMessageBus,
)

pytestmark = pytest.mark.integration

_PROJECT = "proj-rollup"
_PLAN = "plan-rollup"
_ITEM_A = "11111111-1111-5111-8111-111111111111"
_ITEM_B = "22222222-2222-5222-8222-222222222222"


def _item(item_id: str, title: str) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(title),
        description=NotBlankStr("Do the work"),
        acceptance_criteria=(NotBlankStr("it is done"),),
    )


def _child(item_id: str, status: TaskStatus) -> Task:
    return Task(
        id=UUID(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN),
        plan_item_id=UUID(item_id),
        created_by="manager",
        assigned_to=sid("agent-1"),
        status=status,
    )


async def _wired(
    *statuses: tuple[str, TaskStatus],
) -> tuple[TaskEngine, FakePersistenceBackend]:
    """Start a TaskEngine with the rollup registered, seeded with an initiative.

    Returns:
        The started engine and the backing persistence.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    now = datetime(2026, 7, 19, tzinfo=UTC)
    await backend.plans.save(
        Plan(
            id=as_uuid(_PLAN),
            project=NotBlankStr(sid(_PROJECT)),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the initiative"),
            parent_task_id=NotBlankStr(sid("parent-1")),
            items=(_item(_ITEM_A, "Scaffold"), _item(_ITEM_B, "Build")),
            status=PlanStatus.EXECUTING,
            created_at=now,
            updated_at=now,
        )
    )
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(_PLAN),
            status=ProjectStatus.ACTIVE,
        )
    )
    for item_id, status in statuses:
        await backend.tasks.save(_child(item_id, status))

    clock = FakeClock()
    engine = TaskEngine(
        config=TaskEngineConfig(),
        persistence=backend,
        message_bus=EngineMessageBus(),  # type: ignore[arg-type]
    )
    rollup = ProjectRollupService(
        persistence=backend,
        plan_status_writer=PlanService(repo=backend.plans, clock=clock),
        clock=clock,
    )
    engine.register_observer(rollup.on_task_state_changed)
    await engine.start()
    return engine, backend


async def _statuses(
    backend: FakePersistenceBackend,
) -> tuple[PlanStatus, ProjectStatus]:
    plan = await backend.plans.get(NotBlankStr(sid(_PLAN)))
    project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
    assert plan is not None
    assert project is not None
    return plan.status, project.status


class TestInitiativeRollupWiring:
    async def test_last_task_passing_completes_the_initiative(self) -> None:
        engine, backend = await _wired(
            (_ITEM_A, TaskStatus.COMPLETED),
            (_ITEM_B, TaskStatus.IN_REVIEW),
        )
        try:
            # The review gate's decision: the final item passes verification.
            await engine.transition_task(
                _ITEM_B,
                TaskStatus.COMPLETED,
                requested_by="reviewer",
            )
            # Stop drains the observer queue, so the rollup has run by the
            # time the assertions below read the statuses. The finally is the
            # cleanup guard; stop() is idempotent, so both calls are safe.
            await engine.stop()
        finally:
            await engine.stop()

        assert await _statuses(backend) == (
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
        )

    async def test_unverified_task_cannot_complete_the_initiative(self) -> None:
        """Executed but not yet verified must not count as delivered."""
        engine, backend = await _wired(
            (_ITEM_A, TaskStatus.COMPLETED),
            (_ITEM_B, TaskStatus.IN_PROGRESS),
        )
        try:
            # The execution loop hands the task to review; the completion
            # oracle has not ruled on it yet.
            await engine.transition_task(
                _ITEM_B,
                TaskStatus.IN_REVIEW,
                requested_by="execution",
            )
            # Drains the observer queue before the assertions; see above.
            await engine.stop()
        finally:
            await engine.stop()

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )
