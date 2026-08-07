"""Project-delete cascade: resolve a project's children before removing it."""

from typing import Final

from synthorg.api.controllers._task_teardown import terminate_task
from synthorg.api.services.plan_service import PlanService
from synthorg.api.state import AppState
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_PROJECT_CASCADE_COMPLETED,
    API_PROJECT_CASCADE_CONTENDED,
)
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository
from synthorg.persistence.state import persistence_of
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)

_CASCADE_REASON: Final[str] = "project deleted"

#: Bounded re-read budget for a plan the initiative rollup is writing at the
#: same time. The rollup is a legitimate concurrent writer of this row, so a
#: version conflict here is contention, not an error.
_SUPERSEDE_ATTEMPTS: Final[int] = 3


def _retire_target(plan: Plan) -> tuple[PlanStatus, NotBlankStr | None]:
    """Choose the terminal status a teardown may legally write for *plan*.

    SUPERSEDED demands a non-empty item DAG, so a plan still being drafted
    (the state a fresh proposal sits in) cannot be superseded at all: the
    write violates the items CHECK and surfaces as a 500 on an otherwise
    valid project delete. An itemless plan is failed instead, which is the
    status that permits an empty list and which carries the reason.

    Returns:
        The ``(status, failure_reason)`` pair to write.
    """
    if plan.items:
        return PlanStatus.SUPERSEDED, None
    return PlanStatus.FAILED, NotBlankStr(_CASCADE_REASON)


async def _supersede_plan(
    plan_service: PlanService,
    repository: PlanRepository,
    plan: Plan,
    *,
    requested_by: str,
) -> None:
    """Retire *plan*, re-reading if the rollup writes it first.

    The initiative rollup advances the same plan row whenever a task under it
    changes status, so deleting a project while its last task completes can
    lose the race. Without this, the conflict would abort the whole cascade
    mid-loop and surface as a 500 on an otherwise valid delete.
    """
    current = plan
    for _ in range(_SUPERSEDE_ATTEMPTS):
        # Re-derived per attempt: the winner of a lost race may have filled
        # the items, which changes which terminal is legal.
        status, failure_reason = _retire_target(current)
        try:
            await plan_service.sync_status(
                current,
                status,
                requested_by=requested_by,
                reason=_CASCADE_REASON,
                failure_reason=failure_reason,
            )
        except VersionConflictError:
            refreshed = await repository.get(NotBlankStr(str(current.id)))
            if refreshed is None or refreshed.status in TERMINAL_STATUSES:
                # The winner already resolved it; nothing is left orphaned.
                return
            current = refreshed
            continue
        return
    logger.warning(
        API_PROJECT_CASCADE_CONTENDED,
        plan_id=str(plan.id),
        attempts=_SUPERSEDE_ATTEMPTS,
    )


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
    plans_retired = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        plans = await persistence.plans.query(
            PlanFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        for plan in plans:
            if plan.status not in TERMINAL_STATUSES:
                await _supersede_plan(
                    plan_service,
                    persistence.plans,
                    plan,
                    requested_by=requested_by,
                )
                plans_retired += 1
        if len(plans) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    task_engine = task_engine_of(app_state)
    offset = 0
    tasks_cancelled = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        tasks = await persistence.tasks.query(
            TaskFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        for task in tasks:
            if task.status not in TRULY_TERMINAL_STATUSES:
                await terminate_task(
                    task_engine,
                    task,
                    requested_by=requested_by,
                    reason=_CASCADE_REASON,
                )
                tasks_cancelled += 1
        if len(tasks) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    # The one record of how much a delete actually took with it. Without it a
    # cascade over dozens of children is indistinguishable from one over none,
    # and the delete that follows looks like the whole operation.
    logger.info(
        API_PROJECT_CASCADE_COMPLETED,
        project_id=project_id,
        plans_retired=plans_retired,
        tasks_cancelled=tasks_cancelled,
        requested_by=requested_by,
    )
