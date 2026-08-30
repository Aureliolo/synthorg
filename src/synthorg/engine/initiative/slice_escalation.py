# module-kind: service
"""What happens when a slice ask crosses the deterministic autonomy gate.

Parallel to :mod:`stall_escalation`, not a reuse of it: a slice decision and a
stall decision resolve differently (one grafts more work onto a live,
still-executing plan; the other replans or ends the whole initiative) and must
not share one idempotency key, or an operator answering one could be read as
having answered the other.

A stall escalates unconditionally once no automatic route remains, because the
whole initiative has stopped. A slice ask is not that: ``consider_slice``
already applies the master switch and the per-workstream generation cap before
this is ever reached, so by the time a decision is raised here the only open
question is the deterministic autonomy gate, and refusing it never fails the
plan. The workstream simply ends with its scope unmet, surfaced wherever an
unmet objective already surfaces: the judged EVALUATING gate at the tail, not
a second escalation mechanism.
"""

from typing import Final
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.initiative_slice import (
    INITIATIVE_SLICE_ACTION_TYPE,
    LEAF_ID_METADATA_KEY,
    PLAN_ID_METADATA_KEY,
    SLICE_ESCALATION_ACTOR,
    WORKSTREAM_ID_METADATA_KEY,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.engine.review_staffing.notices import DispatcherSource
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_SLICE_ESCALATED,
    INITIATIVE_SLICE_NOTICE_FAILED,
)

logger = get_logger(__name__)

#: The ask's own risk tier, matching ``ActionType.PLAN_EXTEND_WORKSTREAM``'s
#: entry in the shared risk map.
_RISK_LEVEL: Final[ApprovalRiskLevel] = ApprovalRiskLevel.MEDIUM


def _matches(item: ApprovalItem, plan: Plan, leaf: PlanItem) -> bool:
    """Whether *item* is a slice-ask decision for this exact (plan, leaf).

    Provenance as well as subject, on the same reasoning
    ``StallEscalationService.is_open`` checks both: the action type says what
    a decision asks, not who asked it, and anything able to mint one under
    this action type could otherwise be read as this organisation's own
    settled answer.

    Returns:
        ``True`` when *item* is this organisation's decision for this leaf.
    """
    return (
        str(item.requested_by) == SLICE_ESCALATION_ACTOR
        and item.metadata.get(PLAN_ID_METADATA_KEY) == str(plan.id)
        and item.metadata.get(LEAF_ID_METADATA_KEY) == leaf.id
    )


def _describe(workstream: PlanItem, leaf: PlanItem) -> str:
    """Compose what the operator reads on the decision.

    Returns:
        What stopped, why a person is being asked, and what each answer does.
    """
    return (
        f'The workstream "{workstream.title}" finished the tree it was given, '
        f'but "{leaf.title}" was dispatched as one unit even though it still '
        "claimed more scope than one turn could cover "
        f"({leaf.unsplit_reason}).\n\n"
        "Approve to have the organisation plan and build one more increment "
        "for this item alone; the new work runs through the same review gates "
        "as everything else. Reject to leave this workstream as delivered, "
        "with this item's remaining scope unmet."
    )


class SliceEscalationService:
    """Raises, and re-raises exactly once, the decision a slice ask needs.

    Args:
        approvals: Where the decision is parked.
        notifications: Late-bound dispatcher source, called per send.
        clock: Clock seam supplying the decision's creation time.
    """

    __slots__ = ("_approvals", "_clock", "_notifications")

    def __init__(
        self,
        *,
        approvals: ApprovalStoreProtocol,
        notifications: DispatcherSource = None,
        clock: Clock,
    ) -> None:
        self._approvals = approvals
        self._notifications = notifications
        self._clock = clock

    async def status_for(self, plan: Plan, leaf: PlanItem) -> ApprovalStatus | None:
        """The live status of this (plan, leaf)'s slice-ask decision.

        One store scan answers both questions the rollup needs: whether a
        decision is already open (``PENDING``, so the caller must not ask
        again) and whether one was already settled as a rejection (so the
        caller must not raise a second one for a leaf a person has already
        declined). ``None`` covers both "never asked" and "asked, lapsed
        unanswered" (``EXPIRED``), for which asking again is the right thing.

        Returns:
            The decision's status, or ``None`` when none exists yet.
        """
        items = await self._approvals.list_items(
            action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE)
        )
        for item in items:
            if _matches(item, plan, leaf):
                return item.status
        return None

    async def escalate(self, plan: Plan, workstream: PlanItem, leaf: PlanItem) -> None:
        """Put the decision in front of the operator.

        The caller (``drive_slices``) checks :meth:`status_for` first, so this
        only raises a fresh one; it does not re-check idempotency itself,
        exactly as ``StallEscalationService.escalate`` splits the two.
        """
        item = self._build_decision(plan, workstream, leaf)
        await self._approvals.add(item)
        logger.warning(
            INITIATIVE_SLICE_ESCALATED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            approval_id=str(item.id),
        )
        await self._notify(plan, workstream, leaf)

    def _build_decision(
        self, plan: Plan, workstream: PlanItem, leaf: PlanItem
    ) -> ApprovalItem:
        """Build the one decision this slice ask owes the operator.

        Returns:
            The pending ``initiative:slice_ask`` item.
        """
        return ApprovalItem(
            id=uuid4(),
            action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
            title=NotBlankStr(f"Extend workstream: {workstream.title}"),
            description=NotBlankStr(_describe(workstream, leaf)),
            requested_by=NotBlankStr(SLICE_ESCALATION_ACTOR),
            risk_level=_RISK_LEVEL,
            source=ApprovalSource.REVIEW_GATE,
            status=ApprovalStatus.PENDING,
            created_at=self._clock.now(),
            task_id=NotBlankStr(str(plan.parent_task_id)),
            metadata={
                PLAN_ID_METADATA_KEY: str(plan.id),
                WORKSTREAM_ID_METADATA_KEY: workstream.id,
                LEAF_ID_METADATA_KEY: leaf.id,
            },
        )

    async def _notify(self, plan: Plan, workstream: PlanItem, leaf: PlanItem) -> None:
        """Tell the operator once, on the edge that opened the decision."""
        if self._notifications is None:
            return
        dispatcher = self._notifications()
        if dispatcher is None:
            return
        notification = Notification(
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.INFO,
            title=NotBlankStr(f"Extend workstream: {workstream.title}"),
            body=(
                f'"{leaf.title}" may not have delivered its full scope. A '
                "decision is waiting for you: extend it, or leave it as is."
            ),
            source=NotBlankStr(SLICE_ESCALATION_ACTOR),
            metadata={
                PLAN_ID_METADATA_KEY: str(plan.id),
                LEAF_ID_METADATA_KEY: leaf.id,
            },
        )
        try:
            await dispatcher.dispatch(notification)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the decision it announces has already
            # landed, so a failed send is reported rather than allowed to undo
            # it; the operator still finds the item in the queue.
            reraise_critical(exc)
            logger.warning(
                INITIATIVE_SLICE_NOTICE_FAILED,
                plan_id=str(plan.id),
                leaf_id=leaf.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["SliceEscalationService"]
