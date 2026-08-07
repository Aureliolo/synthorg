# module-kind: orchestrator
"""Plan-approval resume flow for the approvals controller.

Owns the ``PLAN_REVIEW`` approval source: on approval, the durable plan the
approval references is rebuilt into a dispatchable subtask tree and handed to
the coordinator (so an operator's edits are exactly what builds), and the
plan's status is synced to APPROVED; on rejection the parent task is cancelled
and the plan is marked REJECTED. Kept separate from the other resume flows so
each stays within its module-size tier. Routing is deterministic off the
persisted :attr:`ApprovalItem.source` discriminator, matching the sibling
resume flows.
"""

from collections.abc import Sequence
from typing import Final

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.controllers._plan_decision_record import record_plan_decisions
from synthorg.api.lifecycle_helpers.plan_review_wiring import PLAN_ID_METADATA_KEY
from synthorg.api.services.plan_service import PlanService
from synthorg.api.state import AppState
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ResourceNotFoundError, VersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.plan_mapping import decomposition_from_plan
from synthorg.engine.initiative.project_writes import link_project_to_plan
from synthorg.engine.state import task_engine_of
from synthorg.hr.state import agent_registry_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PLAN_CHILDREN_FILED,
    APPROVAL_GATE_PLAN_DISPATCH_FAILED,
    APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
    APPROVAL_GATE_PLAN_TASK_TRANSITION_FAILED,
    APPROVAL_GATE_RESUME_TRIGGERED,
)
from synthorg.persistence.state import persistence_of
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)

# Bounded compare-and-swap retries when the durable plan is reworked concurrently
# with its approval sync, so a losing status write re-reads and reapplies rather
# than leaving the plan's status permanently diverged from the recorded decision.
_MAX_STATUS_SYNC_ATTEMPTS: Final[int] = 3


async def try_plan_review_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
) -> bool:
    """Dispatch (or cancel) a decided plan-approval, if this is one.

    Deterministic routing off ``ApprovalItem.source``: only ``PLAN_REVIEW``
    approvals are owned here; everything else returns ``False`` so the caller
    falls through to the parked-context / review-gate flows. Once owned, the
    decision is fully resolved on this path and ``True`` is returned even on
    failure so the approval is never double-handled.

    The decision is reflected onto the durable plan first (APPROVED / REJECTED)
    so the ``/plans`` view matches the recorded decision regardless of what
    happens next. On approval the durable plan (referenced by ``plan_id``) is
    then loaded and rebuilt into a ``DecompositionResult`` dispatched via
    ``coordinate(precomputed_plan=...)``; a dispatch failure marks the parent
    task ``FAILED`` (the plan stays APPROVED, since the decision stands). On
    rejection the parent task is cancelled and nothing builds.

    Returns:
        ``True`` when this flow owns the decision, ``False`` otherwise.

    Raises:
        MemoryError: Re-raised uncaught so a genuine OOM is never masked.
        RecursionError: Re-raised uncaught alongside ``MemoryError``.
    """
    from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415

    item = await _reread_approval_item(app_state, approval_id)
    if item is None or item.source is not ApprovalSource.PLAN_REVIEW:
        return False
    logger.info(
        APPROVAL_GATE_RESUME_TRIGGERED,
        approval_id=approval_id,
        approved=approved,
        note="plan review decision",
    )
    task_id = item.task_id
    plan_id = item.metadata.get(PLAN_ID_METADATA_KEY)
    if not approved:
        await _sync_plan_status(app_state, plan_id, PlanStatus.REJECTED)
        await _cancel_task(app_state, task_id, decided_by)
        return True
    await _sync_plan_status(app_state, plan_id, PlanStatus.APPROVED)
    await _dispatch_approved_plan(
        app_state,
        approval_id=approval_id,
        task_id=task_id,
        plan_id=plan_id,
        decided_by=decided_by,
    )
    return True


async def _resolve_dispatch_inputs(
    app_state: AppState,
    *,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
    decided_by: str,
) -> tuple[MultiAgentCoordinator, Task, Plan] | None:
    """Resolve the three things a dispatch cannot proceed without.

    Each absence is the same outcome reported differently, so they are
    settled together and before anything is written: the approval already
    stands, so a precondition that fails has to fail the task and the plan
    rather than return quietly, and doing that per-check inside the dispatch
    body buried the one path that actually builds.

    Args:
        app_state: Application state.
        approval_id: The decided approval, for the failure record.
        task_id: The parent task the plan decomposes, if the approval named
            one.
        plan_id: The durable plan, if the approval named one.
        decided_by: Who decided, recorded on the failure writes.

    Returns:
        The ``(coordinator, task, plan)`` triple, or ``None`` when one was
        missing and the failure has already been recorded.
    """
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    task = (
        await task_engine_of(app_state).get_task(task_id)
        if coordinator is not None and task_id is not None
        else None
    )
    plan = await persistence_of(app_state).plans.get(plan_id) if plan_id else None
    if coordinator is None or task_id is None:
        why = "coordinator/task missing"
    elif task is None:
        why = "parent task no longer exists"
    elif plan is None:
        why = "durable plan not found"
    else:
        return coordinator, task, plan
    await _fail_dispatch(
        app_state,
        approval_id,
        task_id=task_id,
        plan_id=plan_id,
        decided_by=decided_by,
        why=why,
    )
    return None


async def _file_child_tasks(app_state: AppState, children: Sequence[Task]) -> None:
    """Persist the rebuilt child tasks so the plan's work is queryable.

    Saved rather than created through the engine: the ids are already
    derived from the plan items (``subtask_uuid``), which is what makes a
    re-dispatch of the same plan idempotent, and asking the engine to
    create them would mint new ones and duplicate the tree on every retry.

    One transaction, because a plan's children are a tree and half a tree
    is not a smaller plan: the parent rollup would compute over subtasks
    the plan does not have, and the dispatch that failed marks the plan
    failed while some of its work sits queryable and unowned.

    Args:
        app_state: Application state carrying the persistence backend.
        children: The tasks rebuilt from the approved plan's work items.
    """
    await persistence_of(app_state).tasks.save_many(tuple(children))
    logger.info(
        APPROVAL_GATE_PLAN_CHILDREN_FILED,
        child_count=len(children),
    )


async def _dispatch_approved_plan(
    app_state: AppState,
    *,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
    decided_by: str,
) -> None:
    """Rebuild the durable plan into a subtask tree and dispatch it.

    The approval is already recorded APPROVED and the plan already synced, so
    any failure here marks the parent task ``FAILED`` and drives the plan out
    of its dispatch status rather than silently returning. Both writes matter:
    ``_link_initiative`` moves the plan to EXECUTING before the task tree is
    built (so a rollup mid-dispatch never sees a PLANNING project with tasks
    running), and a dispatch that then fails would otherwise leave the plan
    EXECUTING forever with a failed parent and no children, which nothing
    watches and nothing can move.

    Raises:
        MemoryError: Re-raised uncaught so a genuine OOM is never masked.
        RecursionError: Re-raised uncaught alongside ``MemoryError``.
    """
    resolved = await _resolve_dispatch_inputs(
        app_state,
        approval_id=approval_id,
        task_id=task_id,
        plan_id=plan_id,
        decided_by=decided_by,
    )
    if resolved is None:
        return
    coordinator, task, plan = resolved
    try:
        # Record the plan's decision-items (chosen or recommended-by-default
        # option) into the brain before dispatch, so the company's shaping
        # choices survive the strip-decisions step in ``decomposition_from_plan``
        # rather than vanishing when only work items build.
        await record_plan_decisions(app_state, plan, decided_by=decided_by)
        # Connect the graph before any task starts: the project points at the
        # plan it is executing and goes ACTIVE, and the plan enters EXECUTING.
        # Ordering is load-bearing -- ``coordinate`` below awaits the whole
        # subtask tree, so a rollup event fired mid-dispatch would otherwise
        # observe a project still PLANNING with tasks already running.
        if not await _link_initiative(app_state, plan):
            await _fail_dispatch(
                app_state,
                approval_id,
                task_id=task_id,
                plan_id=plan_id,
                decided_by=decided_by,
                why="project could not be linked to its plan",
            )
            return
        # Dispatch from the durable plan so an operator's edits are exactly
        # what builds; the child task tree is rebuilt deterministically from
        # its items (see ``decomposition_from_plan``).
        decomposition = decomposition_from_plan(plan, parent_task=task)
        # Filed BEFORE dispatch, and the reason is the failure this whole
        # path exists to remove: ``coordinate`` takes the rebuilt tasks by
        # value and never writes them, so an approved plan reached EXECUTING
        # with the children existing only inside the call. Everything that
        # asks afterwards -- the parent rollup reading each subtask's status,
        # the initiative rollup querying a plan's tasks, the dashboard -- goes
        # to the repository, so an unwritten child is one that never
        # happened. Before rather than after so a dispatch that dies partway
        # still leaves the tree it was working on, which is what an operator
        # needs to see to know anything was attempted at all.
        await _file_child_tasks(app_state, decomposition.created_tasks)
        agents = await agent_registry_of(app_state).list_active()
        await coordinator.coordinate(
            CoordinationContext(task=task, available_agents=agents),
            precomputed_plan=decomposition,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- dispatch failure: surface, don't 5xx
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            APPROVAL_GATE_PLAN_DISPATCH_FAILED,
            exc,
            approval_id=approval_id,
            note="approved plan could not be resumed; failing task and plan",
        )
        await _mark_task(
            app_state,
            task_id,
            decided_by,
            target=TaskStatus.FAILED,
            reason="approved plan could not be resumed",
        )
        await _fail_plan(
            app_state,
            plan_id,
            decided_by,
            f"dispatch failed: {safe_error_description(exc)}",
        )


async def _link_initiative(app_state: AppState, plan: Plan) -> bool:
    """Connect the project to the plan it is about to execute.

    Points the project at *plan*, activates it, and moves the plan into
    EXECUTING. Both writes use the same audited paths the rollup uses, so the
    graph has one set of status semantics whether dispatch or rollup is
    writing.

    Returns:
        Whether the project was linked. A failed link must abort the dispatch:
        proceeding would run the whole task tree against a project that never
        learned which plan it is executing, so its progress view would report
        no plan for the life of the initiative and its status would advance
        from PLANNING only by an illegal jump.
    """
    linked = await link_project_to_plan(
        persistence_of(app_state).projects,
        project_id=NotBlankStr(str(plan.project)),
        plan_id=plan.id,
    )
    if linked is None:
        return False
    await _sync_plan_status(app_state, str(plan.id), PlanStatus.EXECUTING)
    return True


async def _fail_dispatch(
    app_state: AppState,
    approval_id: str,
    *,
    task_id: str | None,
    plan_id: str | None,
    decided_by: str,
    why: str,
) -> None:
    """Log an approved-plan dispatch precondition failure; fail task and plan.

    The approval is already persisted APPROVED, so a swallowed failure would
    leave the parent silently stuck in its pre-approval status with no
    board-visible signal. Move it to FAILED so the stuck plan surfaces and
    stays re-runnable (FAILED -> ASSIGNED is valid), and fail the plan too so
    it does not sit in a dispatch status nothing will ever advance.
    """
    logger.error(
        APPROVAL_GATE_PLAN_DISPATCH_FAILED,
        approval_id=approval_id,
        note="approved plan cannot dispatch",
        why=why,
    )
    await _mark_task(
        app_state,
        task_id,
        decided_by,
        target=TaskStatus.FAILED,
        reason=f"approved plan could not be resumed: {why}",
    )
    await _fail_plan(app_state, plan_id, decided_by, f"dispatch failed: {why}")


async def _fail_plan(
    app_state: AppState,
    plan_id: str | None,
    decided_by: str,
    why: str,
) -> None:
    """Drive a plan that cannot dispatch out of its dispatch status.

    Without this the plan rests in APPROVED or EXECUTING with a failed parent
    and no child tasks: a state that can be entered, has no exit, and that
    nothing watches. FAILED is terminal, carries the reason on the plan for
    Plan Review to show, and is reachable from both dispatch statuses.
    """
    await _sync_plan_status(
        app_state,
        plan_id,
        PlanStatus.FAILED,
        requested_by=decided_by,
        failure_reason=NotBlankStr(why),
    )


async def _cancel_task(
    app_state: AppState,
    task_id: str | None,
    decided_by: str,
) -> None:
    """Cancel the parent task of a rejected plan, best-effort."""
    await _mark_task(
        app_state,
        task_id,
        decided_by,
        target=TaskStatus.CANCELLED,
        reason="plan rejected at human approval gate",
    )


async def _mark_task(
    app_state: AppState,
    task_id: str | None,
    decided_by: str,
    *,
    target: TaskStatus,
    reason: str,
) -> None:
    """Transition the plan's parent task, surfacing a failure at ERROR.

    A failure here means the approval decision and the task's real status
    diverge (a rejected plan whose task stays live, or a failed dispatch whose
    task never reached FAILED), which is operationally meaningful: log ERROR so
    the divergence is visible, but do not raise (the decision is already
    persisted; a 5xx here would mislabel an already-recorded decision).
    """
    if task_id is None:
        return
    try:
        await task_engine_of(app_state).transition_task(
            task_id,
            target,
            requested_by=decided_by,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            APPROVAL_GATE_PLAN_TASK_TRANSITION_FAILED,
            task_id=task_id,
            target_status=target.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-decision task transition failed; status may diverge",
        )


async def _plan_exists_for_sync(
    service: PlanService, plan_id: str, status: PlanStatus
) -> bool:
    """Whether the plan is there to sync, reporting why when it is not.

    Both answers are the same outcome for the caller (nothing to write) and
    neither may propagate: the decision is already persisted on the approval,
    so raising would make a retried request re-run the whole resume. Split
    out because it is a lookup and the CAS loop below is a write, and one
    function doing both left the retry the reader is looking for behind two
    unrelated failure branches.

    Args:
        service: The plan service to read through.
        plan_id: The plan the decision named.
        status: The status the sync targets, for the log line.

    Returns:
        ``True`` when the plan was read; ``False`` when it is missing or the
        lookup failed, both already logged.
    """
    try:
        initial = await service.get(plan_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-status sync failed during initial lookup",
        )
        return False
    if initial is None:
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            note="plan-status sync skipped: durable plan not found",
        )
        return False
    return True


async def _sync_plan_status(
    app_state: AppState,
    plan_id: str | None,
    status: PlanStatus,
    *,
    requested_by: str | None = None,
    failure_reason: NotBlankStr | None = None,
) -> None:
    """Reflect an approval decision onto the durable plan's status.

    Routed through :class:`PlanService` so the decision transition gets the
    same ``API_PLAN_*`` audit coverage as an operator edit. The decision is
    already persisted on the approval, so a failure here is logged, not raised.
    A concurrent rework (version conflict) is retried after a re-read, up to
    ``_MAX_STATUS_SYNC_ATTEMPTS``, so a lost CAS write does not leave the
    ``/plans`` status permanently diverged from the recorded decision; a
    persistent conflict is escalated to ERROR (the divergence is not transient).
    """
    if not plan_id:
        return
    service = PlanService(repo=persistence_of(app_state).plans, clock=app_state.clock)
    if not await _plan_exists_for_sync(service, plan_id, status):
        return

    async def read() -> tuple[Plan, int]:
        # Re-read on each attempt so a retry's CAS uses the current version. A
        # plan deleted after the initial fetch aborts the loop cleanly: a write
        # against the stale last-known plan would only spin the CAS retries into
        # a misleading "version conflict" before failing anyway.
        plan = await service.get(plan_id)
        if plan is None:
            msg = "durable plan deleted mid status-sync"
            raise ResourceNotFoundError(msg)
        return plan, plan.version

    async def write(plan: Plan, _version: int) -> None:
        await service.sync_status(
            plan,
            status,
            requested_by=requested_by,
            failure_reason=failure_reason,
        )

    try:
        await CASRetryHandler(
            resource="plan_status_sync", max_attempts=_MAX_STATUS_SYNC_ATTEMPTS
        ).execute(read, write)
    except ResourceNotFoundError:
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            note="plan-status sync skipped: durable plan deleted mid-sync",
        )
    except VersionConflictError:
        logger.error(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            attempts=_MAX_STATUS_SYNC_ATTEMPTS,
            note="plan-status sync lost repeated version conflicts; "
            "/plans status diverges from the recorded decision",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-status sync failed; /plans status may lag the decision",
        )
