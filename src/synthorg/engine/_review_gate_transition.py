"""Committing the task-state transition a review decision asks for.

The decision itself is resolved elsewhere; this is only the commit, and
it carries the two cases where "move the task to *target*" is not one
plain hop.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import BlockedReason, TaskStatus
from synthorg.core.task_transitions import transition_path
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
    APPROVAL_GATE_REVIEW_TRANSITION_SKIPPED,
)

logger = get_logger(__name__)

#: Attribution for a transition the review gate commits on a decision.
REVIEW_GATE_REQUESTED_BY = "review-gate-service"


async def commit_decision_transition(
    task_engine: TaskEngine,
    *,
    task: Task,
    target: TaskStatus,
    transition_reason: str,
    decided_by: str,
    approval_id: str | None,
    blocked_reason: BlockedReason = BlockedReason.ORACLE_ESCALATED,
) -> bool:
    """Commit the review decision's task-state transition.

    A rejected mutation raises here rather than being swallowed as a
    best-effort sync, so the decision is NOT recorded and the caller
    surfaces a real status code instead of a phantom 200.

    A target the task already holds is the exception, and it is not a
    conflict: the decision asks for a state, and that state is the one it
    is in. The state machine refuses a self-transition, so raising here
    turns "already where you asked" into a 409 the operator cannot act on,
    leaving the task parked on an approval that can never be decided.

    Args:
        task_engine: The engine that owns the transition.
        task: The task under decision.
        target: The status the decision asks for.
        transition_reason: Reason recorded against every hop.
        decided_by: The deciding actor, for the log.
        approval_id: The approval row this decided, for the log.
        blocked_reason: Why the task is parked, when the walk reaches BLOCKED.
            Supplied by whichever gate decided, because only it knows: an
            escalation waits on a human while an unstaffed reviewer role waits
            on staffing, and the two are re-entered differently.

    Returns:
        Whether this decision is what moved the task. ``False`` when the
        task was already at *target*, which a reject reaches whenever
        another actor reworked the task first. Deciding and causing are
        different facts, and the decision record alone cannot tell them
        apart, so the caller is handed the difference rather than left to
        infer it.

    Raises:
        TaskEngineError: Any ``transition_task`` failure, logged and
            re-raised (never swallowed).
    """
    if task.status is target:
        logger.info(
            APPROVAL_GATE_REVIEW_TRANSITION_SKIPPED,
            task_id=str(task.id),
            decided_by=decided_by,
            approval_id=approval_id,
            target_status=target.value,
            note="task already holds the decided status; nothing to transition",
        )
        return False
    # An escalation parks the task at BLOCKED, and the human's answer has to
    # rejoin the review it came from, because BLOCKED reaches COMPLETED only
    # through IN_REVIEW: that is what keeps the completion oracle on the single
    # chokepoint it was built for. Only that one bridge is walked. Any other
    # status transitions directly, so a decision on a task that should never
    # have reached this gate still raises the engine's own error naming the
    # illegal edge rather than being marched through the lifecycle to get
    # there.
    hops = _route(task.status, target)
    hop = hops[0]
    try:
        for hop in hops:
            await _take_hop(
                task_engine,
                task_id=str(task.id),
                hop=hop,
                transition_reason=transition_reason,
                blocked_reason=blocked_reason,
            )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            task_id=str(task.id),
            decided_by=decided_by,
            approval_id=approval_id,
            target_status=target.value,
            # The walk is up to two hops, and where it stopped decides what
            # the task is now: failing the bridge leaves it where it started,
            # failing the second leaves it parked at IN_REVIEW. The final
            # target alone cannot tell those apart, and they need different
            # recovery.
            failed_at_status=hop.value,
            stage="transition_task",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    return True


def _route(current: TaskStatus, target: TaskStatus) -> tuple[TaskStatus, ...]:
    """Return the hops this decision takes to reach *target*.

    An escalation parks the task at BLOCKED, and the human's answer has to
    rejoin the review it came from, because BLOCKED reaches COMPLETED only
    through IN_REVIEW: that is what keeps the completion oracle on the single
    chokepoint it was built for. Only that one bridge is walked. Any other
    status transitions directly, so a decision on a task that should never
    have reached this gate still raises the engine's own error naming the
    illegal edge rather than being marched through the lifecycle to get there.

    Args:
        current: The task's status now.
        target: The status the decision asks for.

    Returns:
        The statuses to pass through, ending at *target*.
    """
    if current is TaskStatus.BLOCKED:
        return transition_path(current, target) or (target,)
    return (target,)


async def _take_hop(
    task_engine: TaskEngine,
    *,
    task_id: str,
    hop: TaskStatus,
    transition_reason: str,
    blocked_reason: BlockedReason,
) -> None:
    """Commit one hop of the walk.

    A route to BLOCKED from this gate names WHY, rather than leaving the next
    reader to infer it from a status several unrelated paths also produce. The
    reason comes from the gate that decided, because the gate is the only thing
    that knows whether it is waiting on a human or on staffing.

    Args:
        task_engine: The engine that owns the transition.
        task_id: The task being moved.
        hop: The status to move to.
        transition_reason: Reason recorded against the hop.
        blocked_reason: Why the task is parked, read only on a BLOCKED hop.
    """
    if hop is TaskStatus.BLOCKED:
        await task_engine.transition_task(
            task_id,
            hop,
            requested_by=REVIEW_GATE_REQUESTED_BY,
            reason=transition_reason,
            blocked_reason=blocked_reason,
        )
        return
    await task_engine.transition_task(
        task_id,
        hop,
        requested_by=REVIEW_GATE_REQUESTED_BY,
        reason=transition_reason,
    )
