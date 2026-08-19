# module-kind: service
"""What happens when an initiative has no automatic route left.

Three conditions arrive here and they mean the same thing: no replan trigger is
attached, the operator switched auto-replan off, or the lineage has spent its
generation budget. In every one the organisation cannot get itself unstuck, and
the loop's own rule for that is not to guess: operator attention belongs at the
objective and the workstream, and an initiative reaches it *by exception*, as a
question needing an answer.

Failing the plan here was the tempting answer and is the wrong one. It is the
system deciding whether an initiative the operator may still want should end,
which is a de-escalation of a decision the human owns; and it would not even
fix the surface it looks like it fixes, because the objective task is held at
``IN_PROGRESS`` until the plan COMPLETES, so the initiative's own board row
would not move either.

So this raises one decision, tells the operator once, and leaves the plan
exactly where it is, still replannable by hand while they think. The decision
itself acts: continuing grants one replan on the operator's authority, ending
fails the plan with the stall reason.

Level-triggered like everything else in the rollup. The rollup asks on every
recompute while the condition holds, and the first thing checked here is
whether a decision is already open, because an alert repeated every cadence is
an alert nobody reads.
"""

from collections.abc import Sequence
from typing import Final
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.initiative_stall import (
    DISPOSITION_METADATA_KEY,
    ESCALATION_ACTOR,
    INITIATIVE_STALL_ACTION_TYPE,
    PLAN_ID_METADATA_KEY,
    PROJECT_METADATA_KEY,
    REASON_METADATA_KEY,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.initiative.completion import (
    ItemProgress,
    ReplanDisposition,
    StallReason,
    item_is_done,
)
from synthorg.engine.initiative.ports import PlanStatusWriter
from synthorg.engine.initiative.rollup_plan_advance import advance_plan
from synthorg.engine.review_staffing.notices import DispatcherSource
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_STALL_ALREADY_OPEN,
    INITIATIVE_STALL_ESCALATED,
    INITIATIVE_STALL_NOTICE_FAILED,
    INITIATIVE_STALL_UNDECIDABLE,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

#: Recorded as the requester on the decision. Declared beside the action type
#: rather than here, because the resume flow reads it back as the item's
#: provenance and neither side should own a fact the other has to agree with.
ACTOR: Final[str] = ESCALATION_ACTOR

#: Dead items quoted back to the operator before the description starts
#: counting the rest. Enough to see the shape of the failure, few enough that
#: the decision stays readable in a queue.
_MAX_LISTED_ITEMS: Final[int] = 5


def _humanise(reason: StallReason) -> str:
    """Render a stall reason as the operator reads it.

    Returns:
        The enum's own words with the underscores taken out.
    """
    return reason.value.replace("_", " ")


#: Why each refusal happened, in the operator's words rather than the enum's.
_WHY: Final[dict[ReplanDisposition | None, str]] = {
    None: (
        "automatic replanning is not available in this deployment, "
        "so nothing will revise the plan on its own"
    ),
    ReplanDisposition.DISABLED: (
        "automatic replanning is switched off, "
        "so nothing will revise the plan on its own"
    ),
    ReplanDisposition.BUDGET_EXHAUSTED: (
        "the organisation has already replanned this initiative "
        "as many times as it is allowed to unasked"
    ),
}


def _dead_item_lines(plan: Plan, items: Sequence[ItemProgress]) -> tuple[str, ...]:
    """Name the items that are not going to deliver.

    The title comes off the plan rather than the progress record, which
    carries only ids: an operator asked to decide an initiative's fate reads
    what the work was, never the key it is stored under.

    Returns:
        One line per item that failed or is parked, so the decision says what
        went wrong rather than only that something did.
    """
    titles = {subtask_uuid(item.id): str(item.title) for item in plan.items}
    lines: list[str] = []
    for progress in items:
        if item_is_done(progress):
            continue
        status = (
            progress.task_status.value
            if progress.task_status is not None
            else "never dispatched"
        )
        lines.append(f"{titles.get(progress.item_id, 'untitled item')} ({status})")
    return tuple(lines)


def _describe(
    plan: Plan,
    *,
    reason_text: str,
    why: str,
    items: Sequence[ItemProgress],
) -> str:
    """Compose what the operator reads on the decision.

    Returns:
        The description: what stopped, which items are dead, why nothing
        automatic will fix it, and what each answer does.
    """
    lines = _dead_item_lines(plan, items)
    shown = lines[:_MAX_LISTED_ITEMS]
    hidden = len(lines) - len(shown)
    listed = "\n".join(f"  - {line}" for line in shown)
    more = f"\n  - and {hidden} more" if hidden else ""
    # A tail-stage stall has every item delivered, so an empty list is the
    # honest answer there rather than a section with nothing under it.
    outstanding = (
        f"Outstanding work that will not deliver:\n{listed}{more}\n\n"
        if shown
        else "Every planned item delivered; what stopped is downstream of them.\n\n"
    )
    return (
        f"This initiative can no longer advance: {reason_text}. "
        f"It has stopped, and {why}.\n\n"
        f"{outstanding}"
        "Approve to have the organisation plan it again from what it has "
        "learned; the revised plan comes back to you for approval before any "
        "work starts. Reject to end this initiative, which records why it "
        "stopped. Either way you can still revise the plan yourself from its "
        "own page."
    )


class StallEscalationService:
    """Raises, and re-raises exactly once, the decision a stall needs.

    Args:
        persistence: Backend used by the fail-closed plan write.
        plan_status_writer: The audited plan-status write path, used only
            when nothing in this deployment can ask a human.
        approvals: Where the decision is parked. ``None`` means nothing can
            ask, so the plan is failed with the stall reason instead.
        notifications: Late-bound dispatcher source, called per send.
        clock: Clock seam supplying the decision's creation time.
    """

    __slots__ = (
        "_approvals",
        "_clock",
        "_notifications",
        "_persistence",
        "_plan_writer",
    )

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        plan_status_writer: PlanStatusWriter,
        approvals: ApprovalStoreProtocol | None,
        notifications: DispatcherSource = None,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._plan_writer = plan_status_writer
        self._approvals = approvals
        self._notifications = notifications
        self._clock = clock

    async def escalate(
        self,
        plan: Plan,
        *,
        reason: StallReason,
        disposition: ReplanDisposition | None,
        items: Sequence[ItemProgress],
    ) -> Plan:
        """Put the decision in front of the operator, or fail closed.

        Args:
            plan: The stalled plan.
            reason: The stall shape. Carried onto the decision rather than
                only rendered into it, because re-confirming the stall when
                the answer arrives takes a different form per reason and only
                the reason says which.
            disposition: Which refusal raised this, or ``None`` when no
                trigger is attached at all.
            items: Live item progress, so the decision names what died.

        Returns:
            The plan, unchanged when a decision is open or was just raised,
            and failed when nothing in this deployment can ask a human.
        """
        reason_text = _humanise(reason)
        if self._approvals is None:
            return await self._fail_undecidable(plan, reason_text)
        if await self.is_open(plan):
            return plan
        item = self._build_decision(
            plan, reason=reason, disposition=disposition, items=items
        )
        await self._approvals.add(item)
        logger.warning(
            INITIATIVE_STALL_ESCALATED,
            plan_id=str(plan.id),
            project=str(plan.project),
            approval_id=str(item.id),
            disposition=disposition.value if disposition is not None else "no_trigger",
            generation=plan.replan_generation,
        )
        await self._notify(plan, reason_text=reason_text)
        return plan

    async def is_open(self, plan: Plan) -> bool:
        """Whether a decision for *plan* is already waiting on the operator.

        Read from the store rather than remembered, because the rollup runs
        in a process that restarts and the decision outlives it.

        Public because the caller has its own use for the answer: an
        initiative parked on a person is not one to keep asking the replan
        trigger about, and a refusal logged at WARNING on every pass for the
        life of the stall is the repeating log line the decision replaced.
        One owner, two callers.

        Returns:
            ``True`` when one is pending, so this pass says nothing.
        """
        if self._approvals is None:
            return False
        plan_id = str(plan.id)
        pending = await self._approvals.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_STALL_ACTION_TYPE),
        )
        for item in pending:
            if item.metadata.get(PLAN_ID_METADATA_KEY) == plan_id:
                logger.debug(
                    INITIATIVE_STALL_ALREADY_OPEN,
                    plan_id=plan_id,
                    approval_id=str(item.id),
                )
                return True
        return False

    def _build_decision(
        self,
        plan: Plan,
        *,
        reason: StallReason,
        disposition: ReplanDisposition | None,
        items: Sequence[ItemProgress],
    ) -> ApprovalItem:
        """Build the one decision this stall owes the operator.

        Returns:
            The pending ``initiative:stalled`` item.
        """
        return ApprovalItem(
            id=uuid4(),
            action_type=NotBlankStr(INITIATIVE_STALL_ACTION_TYPE),
            title=NotBlankStr(f"Initiative stopped: {plan.objective_title}"),
            description=NotBlankStr(
                _describe(
                    plan,
                    reason_text=_humanise(reason),
                    why=_WHY[disposition],
                    items=items,
                )
            ),
            requested_by=NotBlankStr(ACTOR),
            # Both answers commit the organisation: one spends another
            # decomposition and another build wave, the other ends work the
            # operator asked for. Neither is routine.
            risk_level=ApprovalRiskLevel.HIGH,
            source=ApprovalSource.REVIEW_GATE,
            status=ApprovalStatus.PENDING,
            created_at=self._clock.now(),
            task_id=NotBlankStr(str(plan.parent_task_id)),
            metadata={
                PLAN_ID_METADATA_KEY: str(plan.id),
                PROJECT_METADATA_KEY: str(plan.project),
                REASON_METADATA_KEY: reason.value,
                DISPOSITION_METADATA_KEY: (
                    disposition.value if disposition is not None else "no_trigger"
                ),
            },
        )

    async def _notify(self, plan: Plan, *, reason_text: str) -> None:
        """Tell the operator once, on the edge that opened the decision.

        Sent here rather than per pass: the decision is the thing needing an
        answer, and repeating the alert every cadence trains an operator to
        ignore it.
        """
        if self._notifications is None:
            return
        dispatcher = self._notifications()
        if dispatcher is None:
            return
        notification = Notification(
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.WARNING,
            title=NotBlankStr(f"Initiative stopped: {plan.objective_title}"),
            body=(
                f"This initiative can no longer advance ({reason_text}) and the "
                "organisation has no way to restart it on its own. A decision "
                "is waiting for you: plan it again, or end it."
            ),
            source=NotBlankStr(ACTOR),
            metadata={PLAN_ID_METADATA_KEY: str(plan.id)},
        )
        try:
            await dispatcher.dispatch(notification)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the decision it announces has already
            # landed, so a failed send is reported rather than allowed to undo
            # it; the operator still finds the item in the queue.
            reraise_critical(exc)
            logger.warning(
                INITIATIVE_STALL_NOTICE_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _fail_undecidable(self, plan: Plan, reason_text: str) -> Plan:
        """Fail a plan nothing in this deployment can ask a human about.

        The same ruling the replan park makes on its own path: a plan nobody
        can decide is worse than a plan that says it stopped, because only the
        second is visible as a problem.

        Returns:
            The failed plan, or the original when the write was refused.
        """
        logger.warning(
            INITIATIVE_STALL_UNDECIDABLE,
            plan_id=str(plan.id),
            project=str(plan.project),
            note="no approvals store; failing the plan rather than parking it",
        )
        return (
            await advance_plan(
                self._persistence,
                self._plan_writer,
                plan,
                PlanStatus.FAILED,
                failure_reason=NotBlankStr(f"initiative stalled: {reason_text}"),
            )
            or plan
        )


__all__ = ["StallEscalationService"]
