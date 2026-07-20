# module-kind: orchestrator
"""Re-plan a dispatched initiative: retire the current plan, open its successor.

Once a plan is dispatched its items are already building, so revising it cannot
be an in-place edit: the tasks implementing the old items would keep running
against a plan that no longer describes them. A re-plan therefore opens a new
revision under review, retires the current one, cancels the work the retired
revision started, and repoints the project at its successor.

Ordering is chosen so a failure never strands the initiative. The successor is
built and persisted FIRST, while nothing is yet retired: opening it only reads
the current revision and inserts a new row, so a failed insert (the likely
failure, a transient write error) leaves the initiative untouched and the
operator simply retries. Only once the successor is durable does the retirement
land, the old work get cancelled (the one irreversible step, since a cancelled
task cannot be un-cancelled), and the project repoint.

``Project.plan_id`` is never ambiguous through this: it names the current
revision until the final relink, and the successor carries no tasks and is
unlinked until then, so the rollup never derives status from it. A true
transaction across the plan service, the task engine, and the project
repository is not available (the task-engine cancellations emit observer
events that cannot be rolled back, and no unit-of-work seam spans the three),
so failure-safe ordering stands in for atomicity.

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
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
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
from synthorg.observability import get_logger
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
    await service.sync_status(
        existing,
        PlanStatus.SUPERSEDED,
        requested_by=requested_by,
        reason=_REPLAN_REASON,
    )
    await _cancel_retired_work(app_state, existing, requested_by=requested_by)
    # The project stays ACTIVE across a re-plan (the initiative is live, it is
    # being re-scoped), so the activation this shares with first dispatch is a
    # no-op here and only the plan pointer moves.
    await link_project_to_plan(
        persistence_of(app_state).projects,
        project_id=NotBlankStr(str(existing.project)),
        plan_id=successor.id,
    )
    logger.info(
        API_PLAN_REPLANNED,
        plan_id=str(successor.id),
        supersedes=str(existing.id),
        project=str(existing.project),
        requested_by=requested_by,
    )
    return successor


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
