# module-kind: service
"""What happens when an extension ask crosses the deterministic autonomy gate.

Parallel to :mod:`stall_escalation`, not a reuse of it: an extension decision
and a stall decision resolve differently (one grafts more work onto a live,
still-executing plan; the other replans or ends the whole initiative) and must
not share one idempotency key, or an operator answering one could be read as
having answered the other.

A stall escalates unconditionally once no automatic route remains, because the
whole initiative has stopped. An extension ask is not that: ``consider_
extension`` already applies the master switch and the per-workstream
generation cap before this is ever reached, so by the time a decision is
raised here the only open question is the deterministic autonomy gate, and
refusing it never fails the plan. The workstream simply ends with its scope
unmet, surfaced wherever an unmet objective already surfaces: the judged
EVALUATING gate at the tail, not a second escalation mechanism.
"""

from typing import Final
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.initiative_extension import (
    EXTENSION_ESCALATION_ACTOR,
    INITIATIVE_EXTENSION_ACTION_TYPE,
    LEAF_ID_METADATA_KEY,
    PLAN_ID_METADATA_KEY,
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
    INITIATIVE_EXTENSION_ALREADY_DECIDED,
    INITIATIVE_EXTENSION_ESCALATED,
    INITIATIVE_EXTENSION_NOTICE_FAILED,
)

logger = get_logger(__name__)

#: A person answering "extend it" or "leave it" is a routine scope decision
#: within one workstream of a plan already approved and running, not a
#: decision that commits or ends the whole initiative the way a stall
#: escalation's HIGH does. Set directly, the same way ``StallEscalationService``
#: sets its own HIGH: this decision's own action type is approval-queue
#: bookkeeping, absent from ``security.risk_map``/``risk_scorer`` (those score
#: the deterministic gate's ``ActionType.PLAN_EXTEND_WORKSTREAM``, a different
#: question), so there is no shared table for this value to drift from.
_RISK_LEVEL: Final[ApprovalRiskLevel] = ApprovalRiskLevel.MEDIUM


def decision_for(
    decisions: tuple[ApprovalItem, ...], leaf: PlanItem
) -> ApprovalItem | None:
    """This organisation's own extension-ask decision for *leaf*, if any.

    Public and module-level (not a method) so a caller holding one plan's
    worth of :meth:`ExtensionEscalationService.open_decisions` can look up
    each of several leaves against it without a second store round trip per
    leaf, which is the shape :func:`~synthorg.engine.initiative.
    rollup_stages.drive_extensions` needs across one workstream's several
    oversized leaves.

    Provenance as well as subject, on the same reasoning
    ``StallEscalationService.is_open`` checks both: the action type says what
    a decision asks, not who asked it, and anything able to mint one under
    this action type could otherwise be read as this organisation's own
    settled answer. *decisions* is already scoped to one plan by
    :meth:`ExtensionEscalationService.open_decisions`, so only the leaf and
    the actor are checked here.

    Returns:
        The matching decision, or ``None`` when none exists for this leaf.
    """
    return next(
        (
            item
            for item in decisions
            if str(item.requested_by) == EXTENSION_ESCALATION_ACTOR
            and item.metadata.get(LEAF_ID_METADATA_KEY) == leaf.id
        ),
        None,
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


class ExtensionEscalationService:
    """Raises, and re-raises exactly once, the decision an extension ask needs.

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

    async def open_decisions(self, plan: Plan) -> tuple[ApprovalItem, ...]:
        """Every extension-ask decision on record for *plan*, any status.

        One store scan, reusable across every leaf one rollup pass checks:
        the naive shape (call :meth:`status_for` once per leaf found needing
        a check) turns one pass into as many scans of the same queue as it
        has leaves to ask about.

        Returns:
            This plan's own extension-ask decisions, in no particular order.
        """
        items = await self._approvals.list_items(
            action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE)
        )
        plan_id = str(plan.id)
        return tuple(
            item for item in items if item.metadata.get(PLAN_ID_METADATA_KEY) == plan_id
        )

    async def status_for(self, plan: Plan, leaf: PlanItem) -> ApprovalStatus | None:
        """The live status of this (plan, leaf)'s extension-ask decision.

        A single-leaf convenience over :meth:`open_decisions`, answering
        every question a caller with one leaf in mind needs: whether a
        decision is already open (``PENDING``, so the caller must not ask
        again), whether one was already settled (``APPROVED``, so the caller
        applies the grant rather than asking; ``REJECTED``, so the caller
        must not raise a second one for a leaf a person has already
        declined), or whether one lapsed unanswered (``EXPIRED``). ``None``
        covers only "never asked"; an expired ask is returned as such rather
        than folded into ``None``, because the two read differently to a
        caller auditing why a decision exists at all, even though both
        currently lead the rollup to ask again.

        Returns:
            The decision's status, or ``None`` when none exists yet.
        """
        decision = decision_for(await self.open_decisions(plan), leaf)
        return decision.status if decision is not None else None

    async def escalate(self, plan: Plan, workstream: PlanItem, leaf: PlanItem) -> None:
        """Put the decision in front of the operator, or note one already is.

        Re-checks idempotency itself rather than trusting the caller, on the
        same reasoning ``StallEscalationService.escalate`` does: the rollup's
        own pre-check and this write are not atomic, so two concurrent
        recomputes finding the same leaf newly in need of an extension could
        otherwise both raise a decision for it.
        """
        if await self.status_for(plan, leaf) is ApprovalStatus.PENDING:
            logger.debug(
                INITIATIVE_EXTENSION_ALREADY_DECIDED,
                plan_id=str(plan.id),
                leaf_id=leaf.id,
                status=ApprovalStatus.PENDING.value,
            )
            return
        item = self._build_decision(plan, workstream, leaf)
        await self._approvals.add(item)
        logger.warning(
            INITIATIVE_EXTENSION_ESCALATED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            approval_id=str(item.id),
        )
        await self._notify(plan, workstream, leaf)

    def _build_decision(
        self, plan: Plan, workstream: PlanItem, leaf: PlanItem
    ) -> ApprovalItem:
        """Build the one decision this extension ask owes the operator.

        Returns:
            The pending ``initiative:extension_ask`` item.
        """
        return ApprovalItem(
            id=uuid4(),
            action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE),
            title=NotBlankStr(f"Extend workstream: {workstream.title}"),
            description=NotBlankStr(_describe(workstream, leaf)),
            requested_by=NotBlankStr(EXTENSION_ESCALATION_ACTOR),
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
            source=NotBlankStr(EXTENSION_ESCALATION_ACTOR),
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
                INITIATIVE_EXTENSION_NOTICE_FAILED,
                plan_id=str(plan.id),
                leaf_id=leaf.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["ExtensionEscalationService", "decision_for"]
