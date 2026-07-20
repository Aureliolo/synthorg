# module-kind: orchestrator
"""Re-plan a dispatched initiative: retire the current plan, open its successor.

Once a plan is dispatched its items are already building, so revising it cannot
be an in-place edit: the tasks implementing the old items would keep running
against a plan that no longer describes them. A re-plan therefore retires the
current revision and opens a new one under review, cancels the work the retired
revision started, and repoints the project at its successor.

Ordering is chosen so the graph is never ambiguous rather than never
incomplete. The retirement happens first, so a project never has two live plans
even if a later step fails; the recoverable state is an initiative whose plan is
superseded and whose successor is missing, which an operator resolves by
planning again. Two live plans would instead leave ``Project.plan_id`` naming
one of them arbitrarily, with the rollup deriving status from a plan the
operator has already abandoned.

The successor enters PENDING_REVIEW, not EXECUTING: its items carry no
approval. Dispatch repoints and activates the project as it does for any first
plan, so approval and re-approval share one path.
"""

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
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
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
    # Reject before the retirement write: a re-plan supersedes the current
    # revision first, so an ineligible plan must fail with nothing persisted.
    require_replannable(existing)
    service = PlanService(repo=persistence_of(app_state).plans, clock=app_state.clock)
    await service.sync_status(
        existing,
        PlanStatus.SUPERSEDED,
        requested_by=requested_by,
        reason=_REPLAN_REASON,
    )
    await _cancel_retired_work(app_state, existing, requested_by=requested_by)
    successor = await service.open_successor(
        existing,
        items=revision.items,
        task_structure=revision.task_structure,
        coordination_topology=revision.coordination_topology,
    )
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

    cancelled = 0
    for task in doomed:
        if task.status not in TRULY_TERMINAL_STATUSES:
            await terminate_task(
                task_engine,
                task,
                requested_by=requested_by,
                reason=_REPLAN_REASON,
            )
            cancelled += 1
    logger.info(
        API_PLAN_REPLANNED,
        plan_id=str(retired.id),
        note="retired work cancelled",
        cancelled=cancelled,
    )
