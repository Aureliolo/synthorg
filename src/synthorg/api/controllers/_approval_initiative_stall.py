# module-kind: orchestrator
"""Resolving the decision a stalled initiative raised.

An approval whose answer changes nothing is the defect one level up, so both
answers act. Approving replans the initiative once on the operator's authority,
which is why the cap and the master switch do not apply to it: both bound what
the organisation does UNASKED, and somebody has just asked. Rejecting fails the
plan with the stall reason, so the initiative ends because a person decided it
should rather than because a rule ran out.

Ownership is decided off the item's ``action_type``, fixed at creation, so every
other approval reads as a miss and falls through untouched. It has to claim the
item before the review-gate flow: a stall decision carries the objective task's
id, and an unclaimed item with a ``task_id`` is read down there as a completion
review and refused.
"""

from typing import Final

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.controllers._plan_resume_writes import sync_plan_status
from synthorg.api.state import AppState
from synthorg.approval.initiative_stall import (
    PLAN_ID_METADATA_KEY,
    is_initiative_stall,
)
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason, stall_reason
from synthorg.engine.initiative.item_progress import collect_item_progress
from synthorg.engine.initiative.ports import ReplanTriggerPort
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.initiative import (
    INITIATIVE_STALL_DECIDED,
    INITIATIVE_STALL_DECISION_STRANDED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

#: What the plan records when the decision was to continue and nothing could.
_NO_TRIGGER: Final[str] = (
    "stalled, and no replan trigger was available to act on the decision"
)


async def _live_stall(app_state: AppState, plan: Plan) -> StallReason | None:
    """Re-derive whether *plan* is still stalled, right now.

    The decision may be hours old. An operator who replanned by hand in the
    meantime, or an item somebody unblocked, means the answer they gave is
    about a plan that no longer exists in that state, and replanning it would
    supersede work that had recovered.

    Returns:
        The live stall shape, or ``None`` when the plan is moving again.
    """
    items = await collect_item_progress(persistence_of(app_state), plan)
    return stall_reason(items)


def _trigger(app_state: AppState) -> ReplanTriggerPort | None:
    """Read whichever replan trigger the rollup holds right now.

    Read live rather than captured, exactly as the EVALUATE stage reads it and
    for the same reason: the coordinator attaches on its own schedule, so a
    trigger resolved at any earlier moment can be a ``None`` that is no longer
    true.

    Returns:
        The attached trigger, or ``None``.
    """
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    return None if rollup is None else rollup.replan_trigger()


async def try_initiative_stall_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
) -> bool:
    """Act on a decided ``initiative:stalled`` approval.

    Args:
        app_state: Application state carrying persistence and the rollup.
        approval_id: The decided approval item's id.
        approved: Whether the operator chose to keep the initiative going.
        decided_by: Who decided, for the audit trail.

    Returns:
        ``True`` when this flow owns the approval, so the caller does not also
        run the parked-context or review-gate flows.
    """
    item = await _reread_approval_item(app_state, approval_id)
    if item is None or not is_initiative_stall(str(item.action_type)):
        return False
    plan_id = item.metadata.get(PLAN_ID_METADATA_KEY, "")
    plan = (
        None
        if not plan_id
        else await persistence_of(app_state).plans.get(NotBlankStr(plan_id))
    )
    if plan is None:
        # Owned, and there is nothing left to do: the plan was deleted or
        # superseded under the operator. Falling through would let the
        # review-gate flow read this item's objective task id as a completion
        # review and refuse it.
        logger.info(
            INITIATIVE_STALL_DECIDED,
            approval_id=approval_id,
            plan_id=plan_id or None,
            approved=approved,
            outcome="plan_gone",
        )
        return True
    reason = await _live_stall(app_state, plan)
    if reason is None:
        logger.info(
            INITIATIVE_STALL_DECIDED,
            approval_id=approval_id,
            plan_id=plan_id,
            approved=approved,
            outcome="no_longer_stalled",
        )
        return True
    if approved:
        await _grant_replan(app_state, plan, reason, decided_by=decided_by)
        return True
    await sync_plan_status(
        app_state,
        plan_id,
        PlanStatus.FAILED,
        requested_by=decided_by,
        failure_reason=NotBlankStr(f"initiative stalled: {reason.value}; ended"),
    )
    logger.info(
        INITIATIVE_STALL_DECIDED,
        approval_id=approval_id,
        plan_id=plan_id,
        approved=False,
        outcome="failed",
    )
    return True


async def _grant_replan(
    app_state: AppState,
    plan: Plan,
    reason: StallReason,
    *,
    decided_by: str,
) -> None:
    """Replan *plan* once on the operator's authority.

    A trigger that has gone away between the decision being raised and taken
    leaves the operator's answer with nothing to act on, which is the state the
    escalation exists to end. So the plan is failed with the reason instead of
    being left reading as though the replan is coming.
    """
    trigger = _trigger(app_state)
    if trigger is None:
        logger.warning(
            INITIATIVE_STALL_DECISION_STRANDED,
            plan_id=str(plan.id),
            note="no replan trigger; failing the plan rather than reporting a "
            "replan that cannot happen",
        )
        await sync_plan_status(
            app_state,
            str(plan.id),
            PlanStatus.FAILED,
            requested_by=decided_by,
            failure_reason=NotBlankStr(_NO_TRIGGER),
        )
        return
    started = await trigger.grant(plan=plan, reason=reason, requested_by=decided_by)
    logger.info(
        INITIATIVE_STALL_DECIDED,
        plan_id=str(plan.id),
        approved=True,
        outcome="granted" if started else "grant_collapsed",
    )


__all__ = ["try_initiative_stall_resume"]
