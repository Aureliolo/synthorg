# module-kind: orchestrator
"""Re-plan a dispatched initiative: retire the current plan, open its successor.

Once a plan is dispatched its items are already building, so revising it cannot
be an in-place edit: the tasks implementing the old items would keep running
against a plan that no longer describes them. A re-plan therefore opens a new
revision under review, retires the current one, cancels the work the retired
revision started, and repoints the project at its successor.

A true transaction across the plan service, the task engine, and the project
repository is not available (the task-engine cancellations emit observer events
that cannot be rolled back, and no unit-of-work seam spans the three), so the
single-live-plan invariant is held by failure-safe ordering plus compensation
rather than by atomicity.

The successor is persisted FIRST, while nothing is yet retired: opening it only
reads the current revision and inserts a new row, so a failed insert (the
likely failure, a transient write error) leaves the initiative untouched and
the operator simply retries. The project is then repointed and the old revision
retired inside a compensated block: both writes are reversible (the link by
relinking, the supersede because a failed ``sync_status`` is atomic and never
lands), so any failure there rolls back to the pre-replan graph, deleting the
orphan successor rather than leaving two live plans. Only past that block, with
the old plan durably superseded and the project already naming the successor,
does the one irreversible step run, cancelling the retired work; a partial
failure there leaves a coherent graph (one live plan the project points at)
with at worst some retired work still running, never a project pointing at a
dead plan.

The successor enters PENDING_REVIEW, not EXECUTING: its items carry no
approval. Dispatch repoints and activates the project as it does for any first
plan, so approval and re-approval share one path.
"""

from collections import Counter
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers._task_teardown import terminate_task
from synthorg.api.services._plan_revision import require_replannable
from synthorg.api.services.plan_service import PlanService
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    CoordinationTopology,
    TaskStatus,
    TaskStructure,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.project_writes import link_project_to_plan
from synthorg.engine.state import task_engine_of
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_PLAN_REPLANNED
from synthorg.persistence.state import persistence_of
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)

_REPLAN_REASON: Final[str] = "plan superseded by a re-plan"


class RevisionInputs(BaseModel):
    """The revised shape a re-plan applies.

    Attributes:
        items: The full revised item list.
        task_structure: Optional override of the classified structure.
        coordination_topology: Optional override of the topology.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    items: tuple[PlanItem, ...] = Field(description="The full revised item list")
    task_structure: TaskStructure | None = Field(
        default=None, description="Optional override of the classified structure"
    )
    coordination_topology: CoordinationTopology | None = Field(
        default=None, description="Optional override of the topology"
    )


async def replan_initiative(
    app_state: AppState,
    existing: Plan,
    *,
    revision: RevisionInputs,
    requested_by: str,
) -> Plan:
    """Retire *existing* and open the revision that replaces it.

    Args:
        app_state: Application state (persistence, clock, task engine).
        existing: The dispatched plan being revised.
        revision: The revised items and optional structure overrides.
        requested_by: Identity recorded on every write.

    Returns:
        The successor plan, awaiting review.

    Raises:
        ConflictError: *existing* is not dispatched, so it is edited in place
            rather than replanned.
        ValidationError: The revised items violate a plan invariant.
    """
    # Reject before any write: an ineligible plan must fail with nothing
    # persisted and nothing retired.
    require_replannable(existing)
    service = PlanService(repo=persistence_of(app_state).plans, clock=app_state.clock)
    # Persist the successor before retiring anything. A failed insert here
    # leaves *existing* EXECUTING and its work running, so the operator retries
    # cleanly rather than being stranded with a superseded plan and no
    # successor.
    successor = await service.open_successor(
        existing,
        items=revision.items,
        task_structure=revision.task_structure,
        coordination_topology=revision.coordination_topology,
    )
    # Repoint the project, then retire the old revision. Both are reversible
    # (the link by relinking, the supersede because a failed sync_status is
    # atomic and never lands), so a failure here compensates back to the
    # pre-replan graph rather than leaving two live plans. The project is
    # repointed FIRST so that once the old plan is durably superseded the
    # project already names the successor; the irreversible cancellation then
    # runs outside this block, where a partial failure leaves a coherent graph
    # (one live plan) rather than a project pointing at a dead one.
    try:
        await link_project_to_plan(
            persistence_of(app_state).projects,
            project_id=NotBlankStr(str(existing.project)),
            plan_id=successor.id,
        )
        await service.sync_status(
            existing,
            PlanStatus.SUPERSEDED,
            requested_by=requested_by,
            reason=_REPLAN_REASON,
        )
    except Exception:
        await _rollback_successor(app_state, existing, successor)
        raise
    await _cancel_retired_work(app_state, existing, requested_by=requested_by)
    logger.info(
        API_PLAN_REPLANNED,
        plan_id=str(successor.id),
        supersedes=str(existing.id),
        project=str(existing.project),
        requested_by=requested_by,
    )
    return successor


async def _rollback_successor(
    app_state: AppState,
    existing: Plan,
    successor: Plan,
) -> None:
    """Undo a partial re-plan whose retirement never completed.

    ``open_successor`` persisted *successor* and the project may have been
    repointed at it, but a later write failed before *existing* was durably
    superseded. Restore the pre-replan graph so the operator retries against an
    unchanged initiative rather than one with two live plans: point the project
    back at the still-live *existing* plan (idempotent when the repoint never
    landed) and then delete the orphan successor.

    The delete is GATED on the relink confirming the project points away from
    the successor: ``link_project_to_plan`` returns the updated project on
    success and ``None`` when the project is missing, contended out, or errored.
    Deleting the successor while the project might still name it would leave
    ``project.plan_id`` dangling at a removed row, which is strictly worse than
    keeping a live orphan. So on an unconfirmed relink the successor is kept and
    the project-delete cascade supersedes it later. Both writes are best-effort:
    failures are logged and swallowed so the original error is the one surfaced.
    """
    persistence = persistence_of(app_state)
    restored: Project | None = None
    try:
        restored = await link_project_to_plan(
            persistence.projects,
            project_id=NotBlankStr(str(existing.project)),
            plan_id=existing.id,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised, rest best-effort
        reraise_critical(exc)
        logger.warning(
            API_PLAN_REPLANNED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            project=str(existing.project),
            note="rollback relink failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    if restored is None:
        # The project may still name the successor; deleting it would dangle
        # project.plan_id. Keep the orphan for the cascade to supersede.
        logger.warning(
            API_PLAN_REPLANNED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            project=str(existing.project),
            note="successor kept: rollback relink unconfirmed",
        )
        return
    try:
        await persistence.plans.delete(NotBlankStr(str(successor.id)))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised, rest best-effort
        reraise_critical(exc)
        logger.warning(
            API_PLAN_REPLANNED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            project=str(existing.project),
            note="rollback successor delete failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _cancel_retired_work(
    app_state: AppState,
    retired: Plan,
    *,
    requested_by: str,
) -> None:
    """Cancel the tasks the retired plan dispatched.

    Their plan items no longer exist as approved work, so leaving them running
    would spend the org's budget on a revision the operator has withdrawn, and
    would keep feeding rollup events for a superseded plan.
    """
    persistence = persistence_of(app_state)
    task_engine = task_engine_of(app_state)
    # Drain every page BEFORE terminating any task. Cancelling as we page
    # would mutate the rows the offset walks over; the ordering happens to be
    # stable today (ORDER BY id, and cancelling changes only status), but a
    # teardown that silently skips live work if that ever stops holding is not
    # worth the one saved list.
    doomed: list[Task] = []
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by plan item count
    while True:
        page = await persistence.tasks.query(
            TaskFilterSpec(plan=retired.id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        doomed.extend(page)
        if len(page) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    # terminate_task re-reads each row and routes it to the right terminal, so
    # the tally counts where work actually landed (a CREATED task is rejected,
    # not cancelled) rather than assuming every termination is a cancellation.
    terminated: Counter[TaskStatus] = Counter()
    for task in doomed:
        if task.status not in TRULY_TERMINAL_STATUSES:
            reached = await terminate_task(
                task_engine,
                task,
                requested_by=requested_by,
                reason=_REPLAN_REASON,
            )
            if reached is not None:
                terminated[reached] += 1
    logger.info(
        API_PLAN_REPLANNED,
        plan_id=str(retired.id),
        note="retired work terminated",
        cancelled=terminated[TaskStatus.CANCELLED],
        rejected=terminated[TaskStatus.REJECTED],
    )
