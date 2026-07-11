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

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.lifecycle_helpers.plan_review_wiring import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.decomposition.plan_mapping import decomposition_from_plan
from synthorg.engine.state import task_engine_of
from synthorg.hr.state import agent_registry_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
    APPROVAL_GATE_RESUME_TRIGGERED,
)
from synthorg.persistence.state import persistence_of
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)


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

    On approval the durable plan (referenced by ``plan_id``) is loaded and
    rebuilt into a ``DecompositionResult`` dispatched via
    ``coordinate(precomputed_plan=...)``, so the tree that builds is exactly
    the (possibly operator-edited) plan under review. On rejection the parent
    task is cancelled and nothing builds. Either decision is reflected onto
    the durable plan's status.

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
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    if coordinator is None or task_id is None:
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            note="approved plan cannot dispatch: coordinator/task missing",
        )
        return True
    task = await task_engine_of(app_state).get_task(task_id)
    if task is None:
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            note="approved plan's parent task no longer exists",
        )
        return True
    try:
        # Dispatch from the durable plan so an operator's edits are exactly
        # what builds; the child task tree is rebuilt deterministically from
        # its items (see ``decomposition_from_plan``).
        plan = await persistence_of(app_state).plans.get(plan_id) if plan_id else None
        if plan is None:
            logger.error(
                APPROVAL_GATE_RESUME_FAILED,
                approval_id=approval_id,
                note="approved plan cannot dispatch: durable plan not found",
            )
            return True
        decomposition = decomposition_from_plan(plan, parent_task=task)
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
            APPROVAL_GATE_RESUME_FAILED,
            exc,
            approval_id=approval_id,
            note="approved plan could not be resumed; marking task failed",
        )
        # The approval is already persisted APPROVED, so a swallowed failure
        # (missing plan, registry lookup, or dispatch) would leave the parent
        # silently stuck in its pre-approval status with no board-visible
        # signal. Move it to FAILED so the stuck plan surfaces and stays
        # re-runnable (FAILED -> ASSIGNED is valid).
        await _mark_task(
            app_state,
            task_id,
            decided_by,
            target=TaskStatus.FAILED,
            reason="approved plan could not be resumed",
        )
        return True
    await _sync_plan_status(app_state, plan_id, PlanStatus.APPROVED)
    return True


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
            APPROVAL_GATE_RESUME_FAILED,
            task_id=task_id,
            target_status=target.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-decision task transition failed; status may diverge",
        )


async def _sync_plan_status(
    app_state: AppState,
    plan_id: str | None,
    status: PlanStatus,
) -> None:
    """Reflect an approval decision onto the durable plan's status.

    Best-effort: the decision is already persisted on the approval, so a
    failure here (plan gone, write error) is logged, not raised. Keeps the
    ``/plans`` view honest without ever mislabelling a recorded decision.
    """
    if not plan_id:
        return
    try:
        backend = persistence_of(app_state)
        plan = await backend.plans.get(plan_id)
        if plan is None:
            logger.warning(
                APPROVAL_GATE_RESUME_FAILED,
                plan_id=plan_id,
                target_status=status.value,
                note="plan-status sync skipped: durable plan not found",
            )
            return
        await backend.plans.update(
            plan.model_copy(
                update={"status": status, "updated_at": app_state.clock.now()},
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-status sync failed; /plans status may lag the decision",
        )
