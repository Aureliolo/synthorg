"""Committing the task-state transition a review decision asks for.

The decision itself is resolved elsewhere; this is only the commit, and
it carries the two cases where "move the task to *target*" is not one
plain hop.
"""

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
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
) -> None:
    """Commit the review decision's task-state transition.

    A rejected mutation raises here rather than being swallowed as a
    best-effort sync, so the decision is NOT recorded and the caller
    surfaces a real status code instead of a phantom 200.

    A target the task already holds is the exception, and it is not a
    conflict: the decision asks for a state, and that state is the one it
    is in. The state machine refuses a self-transition, so raising here
    turned "already where you asked" into a 409 the operator could not act
    on, and the task stayed BLOCKED on an approval that could never be
    decided. Three tasks in one live run reached exactly that, with no
    reachable exit between them.

    Args:
        task_engine: The engine that owns the transition.
        task: The task under decision.
        target: The status the decision asks for.
        transition_reason: Reason recorded against every hop.
        decided_by: The deciding actor, for the log.
        approval_id: The approval row this decided, for the log.

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
        return
    # An escalation parks the task at BLOCKED, and the human's answer has to
    # rejoin the review it came from, because BLOCKED reaches COMPLETED only
    # through IN_REVIEW: that is what keeps the completion oracle on the single
    # chokepoint it was built for. Only that one bridge is walked. Any other
    # status transitions directly, so a decision on a task that should never
    # have reached this gate still raises the engine's own error naming the
    # illegal edge rather than being marched through the lifecycle to get
    # there.
    hops = (
        transition_path(task.status, target) or (target,)
        if task.status is TaskStatus.BLOCKED
        else (target,)
    )
    try:
        for hop in hops:
            await task_engine.transition_task(
                str(task.id),
                hop,
                requested_by=REVIEW_GATE_REQUESTED_BY,
                reason=transition_reason,
            )
    except Exception as exc:
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            task_id=str(task.id),
            decided_by=decided_by,
            approval_id=approval_id,
            target_status=target.value,
            stage="transition_task",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
