"""Project-delete cascade: resolve a project's children before removing it."""

from typing import Final

from synthorg.api.controllers._approval_retire import (
    retiring_approvals_for_tasks,
    retiring_plan_approvals,
)
from synthorg.api.controllers._deletion_record import record_deletion
from synthorg.api.controllers._task_teardown import terminate_task
from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.core.deleted_entity import DeletedEntityKind
from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import TaskNotFoundError
from synthorg.engine.state import task_engine_of
from synthorg.engine.task_engine import TaskEngine
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

    SUPERSEDED demands a non-empty item DAG, so a plan whose decomposition
    has not produced one (a PLANNING plan, whose items arrive at the end of
    that stage) cannot be superseded at all: the write violates the items
    CHECK and surfaces as a 500 on an otherwise valid project delete. An
    itemless plan is failed instead, which is the status that permits an
    empty list and which carries the reason.

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

    Raises:
        ConflictError: The retry budget ran out with the plan still
            non-terminal. Raised rather than logged and returned: the
            caller deletes the project once the cascade reports done, and
            ``plans.project`` carries no foreign key, so a plan counted as
            retired but still live outlives the project as an orphan
            nothing can reach. Contention is transient by definition, so
            the honest answer is to refuse this delete and let the
            operator repeat it.
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
    msg = (
        f"plan {plan.id} is being written concurrently and could not be "
        f"retired in {_SUPERSEDE_ATTEMPTS} attempts; the project was not "
        "deleted. Retry the delete."
    )
    raise ConflictError(msg)


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
    plan_service = build_plan_service(persistence, clock=app_state.clock)
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

    # Retiring a child is not removing it. A terminal status stops a plan
    # advancing; it does not stop the row existing, and every listing endpoint
    # still returns it, naming a project id that no longer resolves. Worse,
    # the cascade retires a plan with items to SUPERSEDED, which is the one
    # status `DELETE /plans/{id}` refuses: leaving them behind would make
    # whether an operator can ever clean up depend on whether the plan
    # happened to have items.
    plans_deleted = await _delete_retired_plans(
        app_state, plan_service, project_id, requested_by=requested_by
    )
    tasks_deleted = await _delete_cancelled_tasks(
        app_state, task_engine, project_id, requested_by=requested_by
    )

    # The one record of how much a delete actually took with it. Without it a
    # cascade over dozens of children is indistinguishable from one over none,
    # and the delete that follows looks like the whole operation.
    logger.info(
        API_PROJECT_CASCADE_COMPLETED,
        project_id=project_id,
        plans_retired=plans_retired,
        tasks_cancelled=tasks_cancelled,
        plans_deleted=plans_deleted,
        tasks_deleted=tasks_deleted,
        requested_by=requested_by,
    )


async def _delete_retired_plans(
    app_state: AppState,
    plan_service: PlanService,
    project_id: NotBlankStr,
    *,
    requested_by: str,
) -> int:
    """Remove every plan of *project_id*, now that each is terminal.

    Plans go before tasks: ``plans.parent_task_id`` is ``ON DELETE
    RESTRICT``, so a task cannot be removed while a plan still names it.

    Returns:
        How many plan rows were removed.

    Raises:
        ConflictError: A plan's review approval was decided while the teardown
            was preparing to remove it.
    """
    persistence = persistence_of(app_state)
    deleted = 0
    # The offset advances past what SURVIVED this page, not past the page:
    # every row removed falls out of the same filter, so the rows behind it
    # shift forward into positions a fixed stride would step over, and they
    # would outlive the project that owned them. Advancing by the survivors
    # is also what stops a row the teardown legitimately refuses from being
    # read forever.
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        plans = await persistence.plans.query(
            PlanFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        removed_here = 0
        for plan in plans:
            # The teardown reaches the plan repository directly rather than
            # through the route that already retires, so without this the
            # approval survives whichever way the operator removed the plan.
            async with retiring_plan_approvals(
                app_state,
                str(plan.id),
            ) as retirement:
                outcome = await plan_service.delete_for_project_teardown(
                    plan, requested_by=requested_by
                )
                if outcome.live_task_count:
                    # Live work refuses this delete by returning, not raising,
                    # so nothing else would put the plan's review approval
                    # back. The plan is still here and still reviewable.
                    continue
                # Gone either way, whether this call removed it or found it
                # already removed, so its approval decides about nothing and
                # the row has left the filter this loop pages through.
                retirement.removed(str(plan.id))
                removed_here += 1
                if outcome.deleted:
                    await record_deletion(
                        persistence,
                        kind=DeletedEntityKind.PLAN,
                        entity_id=str(plan.id),
                        display_name=plan.objective_title,
                        deleted_by=requested_by,
                    )
                    deleted += 1
        if len(plans) < DEFAULT_PAGE_SIZE:
            break
        offset += len(plans) - removed_here
    return deleted


async def _delete_cancelled_tasks(
    app_state: AppState,
    task_engine: TaskEngine,
    project_id: NotBlankStr,
    *,
    requested_by: str,
) -> int:
    """Remove every task of *project_id*, now that each is terminal.

    A task that has already gone is not an error: the delete is idempotent
    forward-recovery, so a re-issued project delete finds fewer children
    and still finishes.

    Returns:
        How many task rows were removed.

    Raises:
        ConflictError: An approval about one of these tasks was decided while
            the teardown was preparing to remove it.
    """
    persistence = persistence_of(app_state)
    deleted = 0
    # Advances past the survivors, for the reason given on the plan loop.
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        tasks = await persistence.tasks.query(
            TaskFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        # Retired for the whole page at once: an approval about a task that
        # has been removed still offers a decision, and the store answers
        # "every pending approval" and nothing narrower, so asking per task
        # would rescan the queue once per row. Settled per task even so: a
        # page that loses its fifth delete after removing four must leave the
        # survivors answerable without resurrecting four approvals that now
        # decide about nothing.
        removed_here = 0
        async with retiring_approvals_for_tasks(
            app_state,
            [str(t.id) for t in tasks],
        ) as retirement:
            for task in tasks:
                try:
                    removed = await task_engine.delete_task(
                        str(task.id),
                        requested_by=requested_by,
                    )
                except TaskNotFoundError:
                    # Already gone, so it has left this filter too and the rows
                    # behind it have shifted forward by one.
                    retirement.removed(str(task.id))
                    removed_here += 1
                    continue
                if removed:
                    retirement.removed(str(task.id))
                    await record_deletion(
                        persistence,
                        kind=DeletedEntityKind.TASK,
                        entity_id=str(task.id),
                        display_name=task.title,
                        deleted_by=requested_by,
                    )
                    deleted += 1
                    removed_here += 1
        if len(tasks) < DEFAULT_PAGE_SIZE:
            break
        offset += len(tasks) - removed_here
    return deleted
