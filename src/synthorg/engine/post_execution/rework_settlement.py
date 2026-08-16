# module-kind: code
"""What happens to a run the review sent back.

A rework verdict leaves the task IN_PROGRESS, and nothing polls that status
once the coordination wave has returned. So the two ends of a bounded re-run
belong together: the context to try again with while rounds remain, and the
FAILED landing when they are spent. Splitting them left the second one
implicit, which is the deadlock the bound exists to remove.
"""

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.loop_rework import (
    REWORK_EXHAUSTED_REASON,
    REWORK_METADATA_KEY,
    continue_rework,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_sync import fail_unresolved_rework


def rework_continuation(
    execution_result: ExecutionResult,
    *,
    rounds_taken: int,
) -> AgentContext | None:
    """Return the context to re-run with, or ``None`` when there is none.

    Args:
        execution_result: The run the review just judged.
        rounds_taken: Rework rounds this dispatch has already taken.

    Returns:
        The context carrying the reviewer's reason, or ``None`` when the
        review did not send the work back or the rework bound is spent. The
        two cases are told apart afterwards by whether the run still carries
        the reason, which only a sent-back run does.
    """
    reason = execution_result.metadata.get(REWORK_METADATA_KEY)
    if not isinstance(reason, str):
        return None
    return continue_rework(
        execution_result.context,
        reason,
        rounds_taken=rounds_taken,
        execution_id=execution_result.context.execution_id,
    )


async def settle_unresolved_rework(
    execution_result: ExecutionResult,
    *,
    agent_id: str,
    task_id: str,
    rounds_taken: int,
    task_engine: TaskEngine | None,
    approval_store: ApprovalStoreProtocol | None,
) -> ExecutionResult:
    """Fail a run that stopped reworking without clearing its review.

    Args:
        execution_result: The last reworked run.
        agent_id: The agent that ran it.
        task_id: The task it ran.
        rounds_taken: How many rounds were spent, for the reason.
        task_engine: Central engine the move syncs to.
        approval_store: Queue the failure item lands in.

    Returns:
        The run unchanged when no rework is outstanding, else a copy whose
        task has been driven to FAILED.
    """
    reason = execution_result.metadata.get(REWORK_METADATA_KEY)
    if not isinstance(reason, str):
        return execution_result
    return await fail_unresolved_rework(
        execution_result,
        agent_id=agent_id,
        task_id=task_id,
        task_engine=task_engine,
        approval_store=approval_store,
        reason=REWORK_EXHAUSTED_REASON.format(rounds=rounds_taken + 1, reason=reason),
    )


__all__ = [
    "rework_continuation",
    "settle_unresolved_rework",
]
