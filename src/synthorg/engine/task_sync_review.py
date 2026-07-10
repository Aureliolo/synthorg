"""Review-approval creation for post-execution task sync.

Extracted from ``task_sync`` so the transition orchestrator stays within
its module-size budget.  Best-effort: a failure to create the approval
item is logged and swallowed so the execution result is never lost.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.run_outcome import RunOutcome, risk_from_task_outcome
from synthorg.core.task import Task
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_CREATED,
)
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR

logger = get_logger(__name__)

_REVIEW_ACTION_TYPE: Final[str] = "review:task_completion"
_FAILED_ACTION_TYPE: Final[str] = "review:task_failed"

# Human phrasing for the approval description, keyed by run outcome.
_OUTCOME_PHRASE: Final[dict[RunOutcome, str]] = {
    RunOutcome.SUCCEEDED: "completed",
    RunOutcome.EMPTY: "completed with no produced artifacts",
    RunOutcome.FAILED: "failed",
}


async def create_review_approval(
    approval_store: ApprovalStoreProtocol | None,
    *,
    agent_id: str,
    task_id: str,
    task: Task,
    outcome: RunOutcome,
) -> str | None:
    """Create an ApprovalItem for a task entering review (or failing).

    Best-effort: failures are logged and swallowed so the execution
    result is never lost.

    The risk level is derived from the task's stakes and the run outcome
    (a high-stakes failure never reads ``LOW``), and a failed run carries
    a distinct action type so the review surface presents it as a failure
    rather than a routine completion. The title uses the task's name so
    the operator never sees a raw UUID.

    Args:
        approval_store: Store to create the item in, or ``None``.
        agent_id: Agent that produced the run under review.
        task_id: Task identifier (matches the approval's ``task_id``).
        task: The task under review (source of stakes + title).
        outcome: The run outcome driving risk level and action type.

    Returns:
        The approval_id on success, or ``None`` if no store or on error.
    """
    if approval_store is None:
        return None

    now = datetime.now(UTC)
    approval_id = uuid4()
    action_type = (
        _FAILED_ACTION_TYPE if outcome is RunOutcome.FAILED else _REVIEW_ACTION_TYPE
    )
    risk_level = risk_from_task_outcome(task.stakes, outcome)
    description = f"Agent {agent_id} {_OUTCOME_PHRASE[outcome]} task: {task.title}"
    # Local import breaks the ontology -> persistence -> budget ->
    # security -> engine -> core.approval cycle (see
    # security.service_escalation for the same pattern).
    from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

    try:
        item = ApprovalItem(
            id=approval_id,
            action_type=action_type,
            title=f"Review: {task.title}",
            description=description,
            requested_by=agent_id,
            risk_level=risk_level,
            created_at=now,
            task_id=task_id,
        )
        await approval_store.add(item)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- approval creation must not lose the execution result
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            approval_id=str(approval_id),
            task_id=task_id,
            agent_id=agent_id,
            context="Failed to create review approval (non-fatal)",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    logger.info(
        APPROVAL_GATE_REVIEW_CREATED,
        approval_id=str(approval_id),
        task_id=task_id,
        agent_id=agent_id,
        outcome=outcome.value,
        risk_level=risk_level.value,
    )
    return str(approval_id)
