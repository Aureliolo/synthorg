"""Shared builders for the initiative-rollup suites.

The rollup answers eleven distinct questions and its tests split by question
rather than by class, so the plan, task and service builders are here: two
copies would let one suite's plan drift from the other's and hide which
difference a failure is actually about.
"""

from datetime import UTC, datetime

from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_stall import (
    INITIATIVE_STALL_ACTION_TYPE,
    PLAN_ID_METADATA_KEY,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.ports import (
    IntegrationPort,
    ReplanTriggerPort,
    RetroCapturePort,
)
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.initiative.stall_escalation import StallEscalationService
from synthorg.engine.review_staffing.notices import DispatcherSource
from synthorg.engine.task_engine import TaskEngine
from tests._shared import FakeClock, as_pk, as_uuid, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

PLAN_ID = "plan-1"
PROJECT = "proj-1"
# Plan item ids must already be canonical UUID strings: ``subtask_uuid`` is
# identity on those, so the item id and its task's id stay the same value.
ITEM_A = sid("item-a")
ITEM_B = sid("item-b")
DECISION = sid("item-decision")


def item(
    item_id: str, *, kind: PlanItemKind = PlanItemKind.WORK, chosen: str | None = None
) -> PlanItem:
    """Build one plan item of the given kind.

    Returns:
        The item, with the artifact expectation a WORK unit must declare.
    """
    options = (
        (
            PlanOption(id="opt-a", title="A", summary="first", recommended=True),
            PlanOption(id="opt-b", title="B", summary="second"),
        )
        if kind is PlanItemKind.DECISION
        else ()
    )
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(
            () if kind is PlanItemKind.DECISION else (NotBlankStr("src/thing.py"),)
        ),
        kind=kind,
        options=options,
        chosen_option_id=chosen,
    )


def plan_of(*items: PlanItem, status: PlanStatus = PlanStatus.EXECUTING) -> Plan:
    """Build the plan under test, carrying *items*.

    Returns:
        The plan at *status*.
    """
    now = datetime(2026, 7, 19, tzinfo=UTC)
    return Plan(
        id=as_uuid(PLAN_ID),
        project=NotBlankStr(sid(PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=items,
        status=status,
        created_at=now,
        updated_at=now,
    )


def task_of(item_id: str, status: TaskStatus) -> Task:
    """Build the task implementing plan item *item_id*.

    Returns:
        The task, keyed so ``subtask_uuid`` identity holds.
    """
    return Task(
        id=as_pk(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(PROJECT),
        plan_id=as_uuid(PLAN_ID),
        plan_item_id=as_pk(item_id),
        created_by="manager",
        assigned_to=sid("agent-1") if status is not TaskStatus.CREATED else None,
        status=status,
    )


async def seed(
    plan: Plan,
    *tasks: Task,
    project_status: ProjectStatus = ProjectStatus.ACTIVE,
    ship_retro_capture: RetroCapturePort | None = None,
    replan_trigger: ReplanTriggerPort | None = None,
    integration: IntegrationPort | None = None,
    task_engine: TaskEngine | None = None,
    approvals: ApprovalStoreProtocol | None = None,
    notifications: DispatcherSource = None,
) -> tuple[ProjectRollupService, FakePersistenceBackend]:
    """Persist *plan* and *tasks* and build the rollup that reads them.

    Returns:
        The rollup and the backend it was built over.
    """
    backend = FakePersistenceBackend()
    await backend.plans.save(plan)
    await backend.projects.save(
        Project(
            id=as_uuid(PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(PLAN_ID),
            status=project_status,
        )
    )
    for task in tasks:
        await backend.tasks.save(task)
    clock = FakeClock()
    service = ProjectRollupService(
        persistence=backend,
        plan_status_writer=build_plan_service(backend, clock=clock),
        clock=clock,
        task_engine=task_engine,
        ship_retro_capture=ship_retro_capture,
        replan_trigger=replan_trigger,
        integration=integration,
    )
    if approvals is not None:
        service.attach_tail(
            stall_escalation=StallEscalationService(
                persistence=backend,
                plan_status_writer=build_plan_service(backend, clock=clock),
                approvals=approvals,
                notifications=notifications,
                clock=clock,
            )
        )
    return service, backend


async def statuses(
    backend: FakePersistenceBackend,
) -> tuple[PlanStatus, ProjectStatus]:
    """Read the plan and project statuses the rollup persisted.

    Returns:
        The pair, both of which must exist.
    """
    plan = await backend.plans.get(NotBlankStr(sid(PLAN_ID)))
    project = await backend.projects.get(NotBlankStr(sid(PROJECT)))
    assert plan is not None
    assert project is not None
    return plan.status, project.status


async def open_decisions(store: ApprovalStoreProtocol) -> tuple[ApprovalItem, ...]:
    """Return every stall decision currently waiting on the operator.

    Returns:
        The pending ``initiative:stalled`` items, in store order.
    """
    return tuple(
        await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_STALL_ACTION_TYPE),
        )
    )


async def decided_plan_ids(store: ApprovalStoreProtocol) -> tuple[str, ...]:
    """Return the plan ids a stall decision is open against.

    Returns:
        One entry per pending decision.
    """
    return tuple(
        str(one.metadata[PLAN_ID_METADATA_KEY]) for one in await open_decisions(store)
    )
