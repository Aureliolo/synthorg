"""Project-delete cascade: resolve a project's children before removing it."""

from typing import Final

from synthorg.api.services.plan_service import PlanService
from synthorg.api.state import AppState
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan_enums import REWORKABLE_STATUSES, PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import VALID_TRANSITIONS
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.state import persistence_of
from synthorg.persistence.task_protocol import TaskFilterSpec

_CASCADE_REASON: Final[str] = "project deleted"


async def cascade_supersede_children(
    app_state: AppState,
    project_id: NotBlankStr,
    *,
    requested_by: str,
) -> None:
    """Supersede a project's live plans and cancel its open tasks before delete.

    A project delete must never orphan its children: every non-terminal plan is
    superseded (a review decision that will now never come) and every
    non-terminal task is cancelled, each through its audited lifecycle
    transition, so no row is left pointing at a deleted project.

    The cascade and the subsequent delete run as separate audited operations,
    not one database transaction: the task-engine transitions emit domain
    events that cannot be rolled back, and no unit-of-work seam spans the plan
    service, the task engine, and the project repository. Consistency comes from
    idempotent forward-recovery instead: the cascade only acts on non-terminal
    children (already-terminal ones are skipped) and the delete runs only after
    it fully succeeds, so a mid-cascade failure or a failed delete leaves a
    retriable, never-orphaning state: re-issuing the delete re-runs the cascade
    as a no-op over the already-resolved children and removes the project. The
    teardown assumes no concurrent child creation for the project being deleted
    (child creation requires a live project; a delete is an exclusive operator
    action), so paginating the existing children is sufficient.

    Args:
        app_state: Application state (carries persistence, clock, task engine).
        project_id: The project whose children are being resolved.
        requested_by: Identity recorded on each task cancellation.
    """
    persistence = persistence_of(app_state)
    plan_service = PlanService(repo=persistence.plans, clock=app_state.clock)
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        plans = await persistence.plans.query(
            PlanFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        for plan in plans:
            if plan.status in REWORKABLE_STATUSES:
                await plan_service.sync_status(
                    plan,
                    PlanStatus.SUPERSEDED,
                    requested_by=requested_by,
                    reason=_CASCADE_REASON,
                )
        if len(plans) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    task_engine = task_engine_of(app_state)
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        tasks = await persistence.tasks.query(
            TaskFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        for task in tasks:
            if task.status not in TRULY_TERMINAL_STATUSES:
                await _terminate_project_task(
                    task_engine, task, requested_by=requested_by
                )
        if len(tasks) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE


async def _terminate_project_task(
    task_engine: TaskEngine,
    task: Task,
    *,
    requested_by: str,
) -> None:
    """Move a non-terminal task to a terminal state on project delete.

    The task lifecycle forbids ``CREATED -> CANCELLED`` (a created task is
    rejected, not cancelled) and lets the stuck states (blocked / failed /
    interrupted / suspended) reach a terminal only via ``ASSIGNED``. This
    routes each task to the correct terminal so no live work dangles against
    the deleted project, and every task keeps its audit row.

    Args:
        task_engine: Engine driving the audited status transitions.
        task: The non-terminal task to terminate.
        requested_by: Identity recorded on each transition.
    """
    target = (
        TaskStatus.REJECTED
        if task.status is TaskStatus.CREATED
        else TaskStatus.CANCELLED
    )
    if target not in VALID_TRANSITIONS[task.status]:
        # A stuck state can only reach a terminal through ASSIGNED; hop there
        # first (the task keeps its assignee), then cancel.
        await task_engine.transition_task(
            str(task.id),
            TaskStatus.ASSIGNED,
            requested_by=requested_by,
            reason=_CASCADE_REASON,
        )
        target = TaskStatus.CANCELLED
    await task_engine.transition_task(
        str(task.id),
        target,
        requested_by=requested_by,
        reason=_CASCADE_REASON,
    )
