"""Review-approval creation for post-execution task sync.

Extracted from ``task_sync`` so the transition orchestrator stays within
its module-size budget.  Best-effort: a failure to create the approval
item is logged and swallowed so the execution result is never lost.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_CREATED,
)
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR

logger = get_logger(__name__)

_REVIEW_ACTION_TYPE: Final[str] = "review:task_completion"


async def create_review_approval(
    approval_store: ApprovalStoreProtocol | None,
    *,
    agent_id: str,
    task_id: str,
) -> str | None:
    """Create an ApprovalItem for a task entering IN_REVIEW.

    Best-effort: failures are logged and swallowed so the
    execution result is never lost.

    Args:
        approval_store: Store to create the item in, or ``None``.
        agent_id: Agent that completed the task.
        task_id: Task identifier.

    Returns:
        The approval_id on success, or ``None`` if no store or on error.
    """
    if approval_store is None:
        return None

    now = datetime.now(UTC)
    approval_id = uuid4()
    # Local import breaks the ontology -> persistence -> budget ->
    # security -> engine -> core.approval cycle (see
    # security.service_escalation for the same pattern).
    from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

    item = ApprovalItem(
        id=approval_id,
        action_type=_REVIEW_ACTION_TYPE,
        title=f"Review task {task_id} completion",
        description=f"Agent {agent_id} completed task {task_id}",
        requested_by=agent_id,
        risk_level=ApprovalRiskLevel.LOW,
        created_at=now,
        task_id=task_id,
    )
    try:
        await approval_store.add(item)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            approval_id=approval_id,
            task_id=task_id,
            agent_id=agent_id,
            error="Failed to create review approval (non-fatal)",
        )
        return None

    logger.info(
        APPROVAL_GATE_REVIEW_CREATED,
        approval_id=str(approval_id),
        task_id=task_id,
        agent_id=agent_id,
    )
    return str(approval_id)
