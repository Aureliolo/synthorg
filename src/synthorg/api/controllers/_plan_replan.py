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

import asyncio
from collections import Counter
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers._plan_input_validation import (
    reject_malformed_tree,
    reject_undecidable_graph,
    reject_unroutable_owners,
)
from synthorg.api.controllers._task_teardown import terminate_task
from synthorg.api.lifecycle_helpers._plan_pending_review_park import (
    review_approvals_for,
)
from synthorg.api.services._plan_revision import require_replannable
from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
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
from synthorg.observability.events.api import (
    API_PLAN_REPLAN_PARK_FAILED,
    API_PLAN_REPLAN_PARKED,
    API_PLAN_REPLAN_ROLLBACK_DELETE_FAILED,
    API_PLAN_REPLAN_ROLLBACK_RELINK_FAILED,
    API_PLAN_REPLAN_ROLLBACK_UNCONFIRMED,
    API_PLAN_REPLAN_WORK_TERMINATED,
    API_PLAN_REPLANNED,
)
from synthorg.persistence.lifecycle_ledger import ledger_for
from synthorg.persistence.state import persistence_of
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)

_REPLAN_REASON: Final[str] = "plan superseded by a re-plan"

#: What the operator reads on a successor nobody can be asked about. Stated as
#: the consequence rather than the cause: what matters to them is that this
#: revision is not going anywhere and the initiative needs a fresh re-plan.
_PARK_FAILED_REASON: Final[str] = (
    "the revision could not be raised for approval, so nothing could decide it"
)


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
    replan_generation: int = 0,
) -> Plan:
    """Retire *existing* and open the revision that replaces it.

    Args:
        app_state: Application state (persistence, clock, task engine).
        existing: The dispatched plan being revised.
        revision: The revised items and optional structure overrides.
        requested_by: Identity recorded on every write.
        replan_generation: Generation stamped on the successor. Zero for a
            human replan (a human decision is not a runaway); the automatic
            trigger passes the predecessor's generation plus one so an
            unattended chain stays capped.

    Returns:
        The successor plan, awaiting review.

    Raises:
        ConflictError: *existing* is not dispatched, so it is edited in place
            rather than replanned.
        ServiceUnavailableError: No roster exists to validate the owners
            against.
        ValidationError: The revised items violate a plan invariant, or own
            an item to a role the org does not staff.
    """
    # Reject before any write: an ineligible plan must fail with nothing
    # persisted and nothing retired. The owners are checked in the same
    # breath and for the same reason, and here rather than in the
    # controller because the automatic replan trigger enters through this
    # function too, with items no human reviewed.
    require_replannable(existing)
    await reject_unroutable_owners(app_state, revision.items)
    reject_undecidable_graph(revision.items, task_structure=revision.task_structure)
    reject_malformed_tree(revision.items)
    service = build_plan_service(persistence_of(app_state), clock=app_state.clock)
    # Persist the successor before retiring anything. A failed insert here
    # leaves *existing* EXECUTING and its work running, so the operator retries
    # cleanly rather than being stranded with a superseded plan and no
    # successor.
    successor = await service.open_successor(
        existing,
        items=revision.items,
        task_structure=revision.task_structure,
        coordination_topology=revision.coordination_topology,
        replan_generation=replan_generation,
    )
    # Repoint the project, then retire the old revision. Both are reversible
    # (the link by relinking, the supersede because a failed sync_status is
    # atomic and never lands), so a failure here compensates back to the
    # pre-replan graph rather than leaving two live plans. The project is
    # repointed FIRST so that once the old plan is durably superseded the
    # project already names the successor; the irreversible cancellation then
    # runs outside this block, where a partial failure leaves a coherent graph
    # (one live plan) rather than a project pointing at a dead one.
    #
    # Shielded, and compensating on BaseException rather than Exception. The
    # automatic replan trigger runs this under a wall-clock deadline, and a
    # cancellation arriving mid-block would otherwise pass straight through an
    # ``except Exception`` handler: the successor would survive with the
    # predecessor never superseded, which is the two-live-plans state this
    # ordering exists to prevent.
    retire = asyncio.ensure_future(
        _retire_predecessor(
            app_state,
            service,
            existing,
            successor,
            requested_by=requested_by,
        )
    )
    try:
        await asyncio.shield(retire)
    except BaseException:
        # The shield let *retire* keep running after our cancellation. Wait
        # for it to settle before compensating so the retire write cannot
        # overlap the rollback write on the same rows (a late repoint landing
        # after the successor is deleted would strand ``project.plan_id``).
        # ``asyncio.wait`` does not cancel the task; shielding it stops our own
        # cancellation from skipping the wait.
        await asyncio.shield(asyncio.wait({retire}))
        if _did_not_commit(retire):
            await asyncio.shield(_rollback_successor(app_state, existing, successor))
            raise
        # Our own cancellation raised out of the shield while the retire it
        # was protecting committed. Rolling back here would undo a write that
        # landed: the project would point at the plan just superseded and the
        # only live one would be deleted. The replan stands, so finish it and
        # let the cancellation through afterwards.
        await asyncio.shield(
            _finish_replan(app_state, existing, successor, requested_by=requested_by)
        )
        raise
    # Shielded for the same reason the tail above is: past this point the
    # supersede is durable and the project already names the successor, so a
    # cancellation landing mid-tail would leave a PENDING_REVIEW plan with no
    # approval parked against it, which nothing re-drives (the recovery sweep
    # reads awaiting-human statuses as parked correctly).
    await asyncio.shield(
        _finish_replan(app_state, existing, successor, requested_by=requested_by)
    )
    logger.info(
        API_PLAN_REPLANNED,
        plan_id=str(successor.id),
        supersedes=str(existing.id),
        project=str(existing.project),
        requested_by=requested_by,
    )
    return successor


async def _abandon_park(
    app_state: AppState,
    service: PlanService,
    successor: Plan,
    written: list[ApprovalItem],
    requested_by: str,
) -> None:
    """Retract a half-written park, then fail the plan it was raised for.

    The store has no batch, so a park that fails partway has already put
    decidable rows in front of an operator: the plan approval still offers
    approve and reject, and answering a question writes back onto a plan
    that is about to be FAILED. Removing them is what makes the failure the
    whole outcome rather than half of one.

    Every step is best-effort and independently guarded. This is the
    compensation, so a failure inside it must not replace the failure that
    caused it, and the FAILED write is attempted even when the retraction
    could not finish: a plan that says it failed is the one thing an
    operator can actually see.

    Args:
        app_state: Application state owning the approval store.
        service: Plan service used for the failing transition.
        successor: The plan whose park could not be raised.
        written: The approval rows that did land, in write order.
        requested_by: Actor recorded on the transition.
    """
    store = app_state.slice(ApprovalStateSlice).store
    for item in written:
        if store is None:
            break
        try:
            await store.delete(NotBlankStr(str(item.id)))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_PLAN_REPLAN_PARK_FAILED,
                plan_id=str(successor.id),
                note="could not retract an approval written before the park failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    try:
        await service.sync_status(
            successor,
            PlanStatus.FAILED,
            requested_by=requested_by,
            failure_reason=NotBlankStr(_PARK_FAILED_REASON),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_PLAN_REPLAN_PARK_FAILED,
            plan_id=str(successor.id),
            note=(
                "the park AND the failing write both failed; the successor is "
                "stranded at pending_review with nothing to decide it"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _did_not_commit(retire: asyncio.Future[None]) -> bool:
    """Report whether the retire failed to land.

    Asked before compensating, because ``asyncio.shield`` raises in the
    AWAITER when the awaiter is cancelled while the shielded task carries on
    and can still succeed. Compensating on the raise alone therefore rolls
    back writes that committed.

    Args:
        retire: The settled retire task.

    Returns:
        ``True`` when the retire was cancelled or raised.
    """
    return retire.cancelled() or retire.exception() is not None


async def _finish_replan(
    app_state: AppState,
    existing: Plan,
    successor: Plan,
    *,
    requested_by: str,
) -> None:
    """Run the tail every committed replan owes, whatever ended the caller.

    Parking runs whatever became of the cancellation. The retirement has
    already committed by the time this is reached, so a failure cancelling the
    predecessor's in-flight work cannot undo the replan: the successor is live
    and ``PENDING_REVIEW``, which recovery reads as parked on a human and
    leaves alone. Skipping the park there would leave an initiative nobody can
    decide, permanently, over a task transition that failed. The cancellation
    error still surfaces afterwards, since the retired work genuinely did not
    stop.

    Args:
        app_state: Application state.
        existing: The plan just superseded.
        successor: The plan now live.
        requested_by: Actor recorded on the writes.
    """
    try:
        await _cancel_retired_work(app_state, existing, requested_by=requested_by)
    finally:
        await _park_for_review(app_state, successor, requested_by=requested_by)


async def _park_for_review(
    app_state: AppState,
    successor: Plan,
    *,
    requested_by: str,
) -> None:
    """Raise the decision *successor*'s ``PENDING_REVIEW`` status promises.

    The status says a person will be asked, and the approvals queue is the
    only surface that asks: there is no approve route on the plans controller
    and the plan page offers only rework, change-request and delete. A
    successor parked nowhere is therefore an initiative that stops for good,
    which a live run proved by leaving one there.

    Failing to park fails the successor, for the reason the first-time gate
    states on its own path: a plan nobody can decide is worse than a plan that
    says it failed, because only the second is visible as a problem.
    """
    # The slice directly, not the 503-raising accessor: this runs inside the
    # automatic trigger's detached task too, where a raise is swallowed and
    # the successor is left in exactly the state this exists to prevent.
    store = app_state.slice(ApprovalStateSlice).store
    if store is None:
        # Nothing asks for approvals in this deployment, so PENDING_REVIEW is
        # not a promise anything made. Left alone rather than failed.
        return
    service = build_plan_service(persistence_of(app_state), clock=app_state.clock)
    parked = review_approvals_for(
        successor,
        requested_by=NotBlankStr(requested_by),
        now=app_state.clock.now(),
    )
    written: list[ApprovalItem] = []
    try:
        for item in parked:
            await store.add(item)
            written.append(item)
    except BaseException as exc:
        reraise_critical(exc)
        logger.warning(
            API_PLAN_REPLAN_PARK_FAILED,
            plan_id=str(successor.id),
            written=len(written),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Shielded: the automatic trigger runs this under a wall-clock
        # deadline, and a cancellation reaching here would otherwise skip the
        # whole compensation and leave exactly the undecidable plan the
        # compensation exists to replace with a visible failure.
        await asyncio.shield(
            _abandon_park(app_state, service, successor, written, requested_by)
        )
        # A failed park is an outcome the plan now carries, so an ordinary
        # failure returns. A cancellation is not an outcome and is never
        # swallowed: the caller is being torn down and has to hear it.
        if not isinstance(exc, Exception):
            raise
        return
    logger.info(
        API_PLAN_REPLAN_PARKED,
        plan_id=str(successor.id),
        approvals=len(parked),
    )


async def _retire_predecessor(
    app_state: AppState,
    service: PlanService,
    existing: Plan,
    successor: Plan,
    *,
    requested_by: str,
) -> None:
    """Repoint the project at *successor*, then supersede *existing*."""
    persistence = persistence_of(app_state)
    await link_project_to_plan(
        persistence.projects,
        project_id=NotBlankStr(str(existing.project)),
        plan_id=successor.id,
        ledger=ledger_for(persistence, clock=app_state.clock),
    )
    await service.sync_status(
        existing,
        PlanStatus.SUPERSEDED,
        requested_by=requested_by,
        reason=_REPLAN_REASON,
    )


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
            ledger=ledger_for(persistence, clock=app_state.clock),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised, rest best-effort
        reraise_critical(exc)
        logger.warning(
            API_PLAN_REPLAN_ROLLBACK_RELINK_FAILED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            project=str(existing.project),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    if restored is None:
        # The project may still name the successor; deleting it would dangle
        # project.plan_id. Keep the orphan for the cascade to supersede.
        logger.warning(
            API_PLAN_REPLAN_ROLLBACK_UNCONFIRMED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            project=str(existing.project),
        )
        return
    try:
        await persistence.plans.delete(NotBlankStr(str(successor.id)))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised, rest best-effort
        reraise_critical(exc)
        logger.warning(
            API_PLAN_REPLAN_ROLLBACK_DELETE_FAILED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            project=str(existing.project),
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
        API_PLAN_REPLAN_WORK_TERMINATED,
        plan_id=str(retired.id),
        cancelled=terminated[TaskStatus.CANCELLED],
        rejected=terminated[TaskStatus.REJECTED],
    )
