# module-kind: orchestrator
"""Plan-approval resume flow for the approvals controller.

Owns the ``PLAN_REVIEW`` approval source: on approval, the exact decomposed
plan parked at gate time is dispatched verbatim (no re-decomposition); on
rejection the parent task is cancelled. Kept separate from the other resume
flows so each stays within its module-size tier. Routing is deterministic off
the persisted :attr:`ApprovalItem.source` discriminator, matching the sibling
resume flows.
"""

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.lifecycle_helpers.plan_review_wiring import PLAN_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.decomposition.models import DecompositionResult
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

    On approval the exact plan parked at gate time is deserialised and
    dispatched via ``coordinate(precomputed_plan=...)`` (no re-decomposition,
    so the built plan matches what the human approved). On rejection the
    parent task is cancelled and nothing builds.

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
    if not approved:
        await _cancel_task(app_state, task_id, decided_by)
        return True
    plan_json = item.metadata.get(PLAN_METADATA_KEY)
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    if coordinator is None or task_id is None or not plan_json:
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            note="approved plan cannot dispatch: coordinator/task/plan missing",
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
        plan = DecompositionResult.model_validate_json(plan_json)
        agents = await agent_registry_of(app_state).list_active()
        await coordinator.coordinate(
            CoordinationContext(task=task, available_agents=agents),
            precomputed_plan=plan,
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
        # (bad stored plan, registry lookup, or dispatch) would leave the parent
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
