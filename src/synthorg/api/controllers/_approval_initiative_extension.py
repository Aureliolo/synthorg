# module-kind: orchestrator
"""Resolving the decision a workstream's extension ask raised.

Parallel to ``_approval_initiative_stall.py``, and simpler: a rejection never
fails the plan, because refusing to extend one workstream is not the whole
initiative running out of road. The workstream simply stays as delivered, and
its own idempotency lives entirely in the approval store (see
``extension_escalation.ExtensionEscalationService.status_for``): once this
resolves the decision, the next rollup pass reads the settled status straight
from the store and never asks again.

Ownership is decided off the item's ``action_type``, fixed at creation, so
every other approval reads as a miss and falls through untouched, on the same
reasoning ``try_initiative_stall_resume`` claims one.
"""

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.lifecycle_helpers.run_recovery_wiring import drive_plan_waves
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_extension import (
    EXTENSION_ESCALATION_ACTOR,
    LEAF_ID_METADATA_KEY,
    PLAN_ID_METADATA_KEY,
    WORKSTREAM_ID_METADATA_KEY,
    is_initiative_extension_ask,
)
from synthorg.core.actor_context import ActorKind, current_actor
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.ports import DriveOutcome, ReplanTriggerPort
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.initiative import (
    INITIATIVE_EXTENSION_DECIDED,
    INITIATIVE_EXTENSION_FOREIGN,
    INITIATIVE_EXTENSION_NOT_GRANTED,
    INITIATIVE_EXTENSION_STALE_DECISION,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


def _trigger(app_state: AppState) -> ReplanTriggerPort | None:
    """Read whichever replan trigger the rollup holds right now.

    Returns:
        The attached trigger, or ``None``.
    """
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    return None if rollup is None else rollup.replan_trigger()


def _item_by_id(plan: Plan, item_id: str) -> PlanItem | None:
    """Find the plan item named *item_id*, or ``None``.

    Returns:
        The item, or ``None`` when it no longer exists.
    """
    return next((item for item in plan.items if item.id == item_id), None)


def _decided_by_a_person() -> bool:
    """Whether a human principal is the one answering.

    Returns:
        ``True`` only for a bound HUMAN actor.
    """
    actor = current_actor()
    return actor is not None and actor.kind is ActorKind.HUMAN


async def try_initiative_extension_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
) -> bool:
    """Act on a decided ``initiative:extension_ask`` approval.

    Args:
        app_state: Application state carrying persistence and the rollup.
        approval_id: The decided approval item's id.
        approved: Whether the operator chose to extend the workstream.
        decided_by: Who decided, for the audit trail.

    Returns:
        ``True`` when this flow owns the approval, so the caller does not also
        run the parked-context or review-gate flows.
    """
    item = await _reread_approval_item(app_state, approval_id)
    if item is None or not is_initiative_extension_ask(str(item.action_type)):
        return False
    if str(item.requested_by) != EXTENSION_ESCALATION_ACTOR:
        # See ``try_initiative_stall_resume``'s identical check: the action
        # type says what a decision asks, not who asked it.
        logger.warning(
            INITIATIVE_EXTENSION_FOREIGN,
            approval_id=approval_id,
            requested_by=str(item.requested_by),
            plan_id=item.metadata.get(PLAN_ID_METADATA_KEY),
            leaf_id=item.metadata.get(LEAF_ID_METADATA_KEY),
        )
        return True
    expected = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    if item.status is not expected:
        logger.warning(
            INITIATIVE_EXTENSION_STALE_DECISION,
            approval_id=approval_id,
            status=item.status.value,
            approved=approved,
            plan_id=item.metadata.get(PLAN_ID_METADATA_KEY),
            leaf_id=item.metadata.get(LEAF_ID_METADATA_KEY),
        )
        return True
    if not approved:
        logger.info(
            INITIATIVE_EXTENSION_DECIDED, approval_id=approval_id, approved=False
        )
        return True
    await _grant(app_state, item, approval_id=approval_id, decided_by=decided_by)
    return True


async def _grant_target(
    app_state: AppState,
    item: ApprovalItem,
    *,
    approval_id: str,
    plan_id: str,
) -> tuple[Plan, PlanItem, PlanItem] | None:
    """Resolve the plan, leaf and workstream a grant decision names.

    Each early exit logs its own outcome, so the caller only needs to check
    for ``None``.

    Returns:
        ``(plan, leaf, workstream)``, or ``None`` when any of them no longer
        resolves.
    """
    plan = (
        None
        if not plan_id
        else await persistence_of(app_state).plans.get(NotBlankStr(plan_id))
    )
    if plan is None:
        logger.info(
            INITIATIVE_EXTENSION_DECIDED,
            approval_id=approval_id,
            plan_id=plan_id or None,
            approved=True,
            outcome="plan_gone",
        )
        return None
    leaf = _item_by_id(plan, item.metadata.get(LEAF_ID_METADATA_KEY, ""))
    # ``unsplit_reason`` is never cleared once written (see ``PlanItem``'s own
    # field docs), so it cannot tell a leaf that was already extended by
    # another writer apart from one that never was. Whether the leaf already
    # has children is the actual "already extended" fact:
    # ``workstream_needs_extension`` excludes a container from the automatic
    # route on the same check.
    if (
        leaf is None
        or leaf.unsplit_reason is None
        or PlanTree.of(plan.items).is_container(leaf.id)
    ):
        logger.info(
            INITIATIVE_EXTENSION_DECIDED,
            approval_id=approval_id,
            plan_id=plan_id,
            approved=True,
            outcome="leaf_already_extended_or_gone",
        )
        return None
    workstream = _item_by_id(plan, item.metadata.get(WORKSTREAM_ID_METADATA_KEY, ""))
    if workstream is None:
        logger.warning(
            INITIATIVE_EXTENSION_NOT_GRANTED,
            plan_id=plan_id,
            leaf_id=leaf.id,
            note="workstream metadata no longer resolves; nothing can be grafted",
        )
        return None
    return plan, leaf, workstream


async def _grant(
    app_state: AppState,
    item: ApprovalItem,
    *,
    approval_id: str,
    decided_by: str,
) -> None:
    """Extend the workstream this decision names, on the operator's authority.

    Falls back to the org's own unasked authority (cap and switch intact) for
    a non-human decider, on the same reasoning ``_grant_replan`` applies: only
    a person's decision lifts a guard that bounds what the org may do alone.
    """
    plan_id = item.metadata.get(PLAN_ID_METADATA_KEY, "")
    target = await _grant_target(
        app_state, item, approval_id=approval_id, plan_id=plan_id
    )
    if target is None:
        return
    plan, leaf, workstream = target
    trigger = _trigger(app_state)
    if trigger is None:
        logger.warning(
            INITIATIVE_EXTENSION_NOT_GRANTED,
            plan_id=plan_id,
            leaf_id=leaf.id,
            note="no replan trigger; nothing can graft the extension",
        )
        return

    async def drive(plan: Plan) -> DriveOutcome:
        return await drive_plan_waves(app_state, plan)

    if not _decided_by_a_person():
        await trigger.consider_extension(
            plan=plan,
            tree=PlanTree.of(plan.items),
            workstream=workstream,
            leaf=leaf,
            drive=drive,
        )
        return
    started = await trigger.grant_extension(
        plan=plan,
        workstream=workstream,
        leaf=leaf,
        drive=drive,
        requested_by=decided_by,
    )
    logger.info(
        INITIATIVE_EXTENSION_DECIDED,
        approval_id=approval_id,
        plan_id=plan_id,
        approved=True,
        outcome="granted" if started else "grant_collapsed",
    )


__all__ = ["try_initiative_extension_resume"]
