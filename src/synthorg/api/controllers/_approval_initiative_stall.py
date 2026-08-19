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
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_stall import (
    ESCALATION_ACTOR,
    PLAN_ID_METADATA_KEY,
    REASON_METADATA_KEY,
    is_initiative_stall,
)
from synthorg.core.actor_context import ActorKind, current_actor
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import (
    ITEM_DERIVED_STALLS,
    STAGE_OF_STALL_REASON,
    StallReason,
    stall_reason,
)
from synthorg.engine.initiative.item_progress import collect_item_progress
from synthorg.engine.initiative.ports import ReplanTriggerPort
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.initiative import (
    INITIATIVE_STALL_DECIDED,
    INITIATIVE_STALL_DECISION_STRANDED,
    INITIATIVE_STALL_FOREIGN,
    INITIATIVE_STALL_NOT_GRANTED,
    INITIATIVE_STALL_STALE_DECISION,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

#: What the plan records when the decision was to continue and nothing could.
_NO_TRIGGER: Final[str] = (
    "stalled, and no replan trigger was available to act on the decision"
)


async def _live_stall(
    app_state: AppState, plan: Plan, recorded: StallReason | None
) -> StallReason | None:
    """Re-confirm whether *plan* is still stalled, right now.

    The decision may be hours old. An operator who replanned by hand in the
    meantime, or an item somebody unblocked, means the answer they gave is
    about a plan that no longer exists in that state, and replanning it would
    supersede work that had recovered.

    Confirming takes one of two forms and only the recorded reason says which,
    which is why the escalation writes it down. An item-derived stall is
    re-derived over the live items. A tail-stage verdict cannot be: every item
    IS done when integration fails or the objective goes unmet, so deriving
    over items answers "recovered" for the one case it is least true of, and
    the decision a person just made would quietly do nothing. Those are
    confirmed by the plan still sitting in the stage that produced the verdict.

    Args:
        app_state: Application state, for the item read.
        plan: The freshly read plan the decision is about.
        recorded: The reason the decision was raised for, when the item
            recorded one. ``None`` falls back to the item derivation, which is
            the honest answer when nothing says otherwise.

    Returns:
        The confirmed stall shape, or ``None`` when the plan is moving again.
    """
    if recorded is not None and recorded not in ITEM_DERIVED_STALLS:
        stage = STAGE_OF_STALL_REASON[recorded]
        return recorded if plan.status is stage else None
    items = await collect_item_progress(persistence_of(app_state), plan)
    return stall_reason(items)


def _recorded_reason(item: ApprovalItem) -> StallReason | None:
    """Read the stall reason the escalation wrote onto the decision.

    Returns:
        The reason, or ``None`` when the item carries none or carries a value
        no longer in the vocabulary (an item outliving a rename is a fact
        about the past, not something to fail a person's decision over).
    """
    raw = item.metadata.get(REASON_METADATA_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return StallReason(raw)
    except ValueError:
        return None


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
    if str(item.requested_by) != ESCALATION_ACTOR:
        # The action type says what a decision asks; it does not say who asked.
        # ``POST /approvals`` takes an action type and a metadata blob from any
        # caller holding write access, and REVIEW_GATE is the default source,
        # so an item minted there is otherwise indistinguishable from one this
        # organisation raised. Acting on it would let a writer aim a plan
        # failure, or a budget-lifting replan, at any initiative they name, and
        # dress it in words of their own on the operator's plan page.
        logger.warning(
            INITIATIVE_STALL_FOREIGN,
            approval_id=approval_id,
            requested_by=str(item.requested_by),
        )
        return False
    expected = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    if item.status is not expected:
        # The answer being acted on and the answer on the row disagree, so one
        # of them is not the decision a person took. Owned and refused:
        # falling through would hand an item carrying the objective task's id
        # to the review-gate flow, which reads it as a completion review.
        logger.warning(
            INITIATIVE_STALL_STALE_DECISION,
            approval_id=approval_id,
            status=item.status.value,
            approved=approved,
        )
        return True
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
    reason = await _live_stall(app_state, plan, _recorded_reason(item))
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
    if not _decided_by_a_person():
        # ``grant`` lifts the operator's switch AND cap and restarts the
        # generation count, on the sole justification that a person asked.
        # Nothing but the actor establishes that, so a decision reaching here
        # from anything else gets the unasked authority instead: the org's own
        # budget applies, exactly as it would with nobody asking.
        actor = current_actor()
        logger.warning(
            INITIATIVE_STALL_NOT_GRANTED,
            plan_id=str(plan.id),
            actor_kind=actor.kind.value if actor is not None else "unbound",
            note="a non-human decision cannot lift the operator's replan cap",
        )
        await trigger.consider(plan=plan, reason=reason)
        return
    started = await trigger.grant(plan=plan, reason=reason, requested_by=decided_by)
    logger.info(
        INITIATIVE_STALL_DECIDED,
        plan_id=str(plan.id),
        approved=True,
        outcome="granted" if started else "grant_collapsed",
    )


def _decided_by_a_person() -> bool:
    """Whether a human principal is the one answering.

    Returns:
        ``True`` only for a bound HUMAN actor. An unbound scope, a SYSTEM
        sweep and an AGENT all answer ``False``, because the authority that
        lifts the cap is a person's and nothing else can stand in for it.
    """
    actor = current_actor()
    return actor is not None and actor.kind is ActorKind.HUMAN


__all__ = ["try_initiative_stall_resume"]
