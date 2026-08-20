# module-kind: service
"""What happens to an initiative that cannot advance on its own.

The rollup decides that a plan is stalled; this decides what is done about it.
Kept beside the rollup rather than inside it because the same answer is reached
two ways: a derivation over the plan's items finds a stall and asks the trigger
here, while a tail stage's verdict is invisible to any such derivation (every
item IS done when integration fails or the objective goes unmet) and so asks
the trigger itself, with the judged evidence a replan brief wants, and arrives
with the answer already in hand.

Nothing here raises. Both entry points are reachable from the plan-review HTTP
path, and a degraded approvals store must not answer an operator a 500 on a
request that has nothing to do with the stall. Escalation is level-triggered,
so a pass that fails costs a delay rather than the decision.
"""

from collections.abc import Awaitable, Callable, Sequence

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import (
    REPLAN_IN_PROGRESS_DISPOSITIONS,
    ItemProgress,
    ReplanDisposition,
    StallReason,
)
from synthorg.engine.initiative.ports import ReplanTriggerPort
from synthorg.engine.initiative.stall_escalation import StallEscalationService
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.initiative import (
    INITIATIVE_STALL_ESCALATION_FAILED,
)

logger = get_logger(__name__)

#: Drives the plan to FAILED with the given reason, answering the persisted
#: plan or ``None`` when the transition was refused. Supplied by the rollup,
#: which owns the audited write path.
FailPlan = Callable[[Plan, NotBlankStr], Awaitable[Plan | None]]


async def route_stall(
    plan: Plan,
    reason: StallReason,
    *,
    items: Sequence[ItemProgress] | None,
    trigger: ReplanTriggerPort | None,
    escalation: StallEscalationService | None,
    fail_plan: FailPlan,
) -> Plan:
    """Ask whether an automatic route remains, and act on the answer.

    The single site that turns "this initiative cannot advance" into something
    happening. Whether the org may replan unasked is the trigger's decision, so
    it is asked rather than inferred from its presence: reading an attached
    trigger as a replan that will happen is what left one initiative scheduling
    a refused replan on every pass while its plan read ``executing`` with every
    item dead.

    Three answers mean no automatic route remains and they escalate
    identically, differing only in the reason the operator is given: no trigger
    is attached, the operator switched auto-replan off, or the lineage has
    spent its generation budget.

    Args:
        plan: The stalled plan.
        reason: The stall shape.
        items: Live item progress, so a raised decision names what died.
        trigger: The replan trigger, when one is attached.
        escalation: The escalation service, when one is attached.
        fail_plan: How to fail the plan when nothing can ask a person.

    Returns:
        The plan, as the route left it.
    """
    if await _decision_open(plan, reason, escalation=escalation):
        # Already in front of a person. Asking the trigger again would refuse
        # again, at WARNING, on every pass for the life of the stall, which is
        # the repeating log line the decision replaced; and nothing automatic
        # may run while somebody is deciding whether the initiative continues
        # at all.
        return plan
    disposition: ReplanDisposition | None = None
    if trigger is not None:
        disposition = await trigger.consider(plan=plan, reason=reason)
        if disposition in REPLAN_IN_PROGRESS_DISPOSITIONS:
            return plan
    return await escalate_stall(
        plan,
        reason,
        disposition=disposition,
        items=items,
        escalation=escalation,
        fail_plan=fail_plan,
    )


async def escalate_stall(
    plan: Plan,
    reason: StallReason,
    *,
    disposition: ReplanDisposition | None,
    items: Sequence[ItemProgress] | None,
    escalation: StallEscalationService | None,
    fail_plan: FailPlan,
) -> Plan:
    """Act on a stall nothing automatic will clear.

    The acting half of :func:`route_stall`, separate because a tail stage asks
    the trigger itself and then has the same answer to act on. One owner for
    what happens next; two callers that arrived at it differently.

    Args:
        plan: The stalled plan.
        reason: The stall shape.
        disposition: What the trigger answered, or ``None`` when none was
            attached to ask.
        items: Live item progress, so the decision names what died.
        escalation: The escalation service, when one is attached.
        fail_plan: How to fail the plan when nothing can ask a person.

    Returns:
        The plan, as the escalation left it.
    """
    if escalation is None:
        # Nothing owns the escalation in this deployment, so the plan is
        # driven out of its dispatch status rather than parked: a plan whose
        # every task failed sitting in EXECUTING with no work left to execute
        # is a state no later event can repair.
        failed = await fail_plan(
            plan, NotBlankStr(f"initiative stalled: {reason.value}")
        )
        return failed or plan
    try:
        return await escalation.escalate(
            plan,
            reason=reason,
            disposition=disposition,
            items=items if items is not None else (),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the escalation is level-triggered, so the
        # next pass re-asks and the decision still lands.
        reraise_critical(exc)
        _log_failure(exc, plan, reason)
        return plan


async def _decision_open(
    plan: Plan,
    reason: StallReason,
    *,
    escalation: StallEscalationService | None,
) -> bool:
    """Whether a decision about *plan* is already waiting on a person.

    Returns:
        ``True`` when one is open. A read that fails answers ``False``, which
        falls through to the escalation, whose own idempotency check is the
        authority; the opposite reading (assume one is open) would silence the
        escalation for as long as the store stayed degraded.
    """
    if escalation is None:
        return False
    try:
        return await escalation.is_open(plan)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the escalation re-asks the same store under
        # its own guard, so the failure is reported once and the decision still
        # lands on a later pass.
        reraise_critical(exc)
        _log_failure(exc, plan, reason)
        return False


def _log_failure(exc: Exception, plan: Plan, reason: StallReason) -> None:
    """Report a degraded escalation without leaking the driver's message."""
    log_exception_redacted(
        logger,
        INITIATIVE_STALL_ESCALATION_FAILED,
        exc,
        plan_id=str(plan.id),
        stall_reason=reason.value,
    )


__all__ = ["FailPlan", "escalate_stall", "route_stall"]
