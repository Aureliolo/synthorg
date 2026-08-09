"""Ask a human whether a run that ran out of turns should carry on.

A run parks here with its work intact and its workspace preserved, which is
the whole point of parking rather than failing. But a park nobody is told
about is a quieter deadlock than the failure it replaced: a clarification or
a decision fork arrives with an approval the agent already raised through its
tool, and a spent turn budget has no such author. So the question is raised
here, by the one component that knows the budget ran out.
"""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_CREATED,
    APPROVAL_GATE_REVIEW_STORE_FAILED,
)

logger = get_logger(__name__)

#: Its own action type, not the review family's: an autonomy grant written
#: for "review this deliverable" must not silently also mean "spend another
#: four turn budgets on it".
_ACTION_TYPE = "execution:extend_turns"


async def raise_turn_ceiling_approval(
    ctx: AgentContext,
    *,
    agent_id: str,
    task_id: str,
    approval_store: ApprovalStoreProtocol | None,
) -> str | None:
    """Raise the extend-or-stop question for a run that spent its budget.

    Best-effort, like its sibling on the review path: the run has already
    parked and its result must not be lost to a store failure.

    Args:
        ctx: Context of the parked run, for the turn counts the operator
            needs to judge whether more turns are worth it.
        agent_id: Agent whose run parked.
        task_id: Task the run was working on.
        approval_store: Store to raise the question in, or ``None``.

    Returns:
        The approval id, or ``None`` when there is no store or the write
        failed.
    """
    if approval_store is None:
        return None
    task_execution = ctx.task_execution
    title = task_execution.task.title if task_execution is not None else task_id
    approval_id = uuid4()
    # Local import breaks the ontology -> persistence -> budget -> security
    # -> engine -> core.approval cycle (see security.service_escalation).
    from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

    item = ApprovalItem(
        id=approval_id,
        action_type=_ACTION_TYPE,
        title=f"Out of turns: {title}",
        description=(
            f"Agent {agent_id} used {ctx.turn_count} turns across "
            f"{ctx.turn_extensions_granted} extension(s) without finishing "
            "the task. Its workspace and everything it wrote are intact. "
            "Approve to carry on, reject to stop here."
        ),
        requested_by=agent_id,
        risk_level=ApprovalRiskLevel.MEDIUM,
        created_at=datetime.now(UTC),
        task_id=task_id,
    )
    try:
        await approval_store.add(item)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the run already parked; losing the
        # question must not also lose the result
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_REVIEW_STORE_FAILED,
            approval_id=str(approval_id),
            task_id=task_id,
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    logger.info(
        APPROVAL_GATE_REVIEW_CREATED,
        approval_id=str(approval_id),
        task_id=task_id,
        agent_id=agent_id,
        action_type=_ACTION_TYPE,
    )
    return str(approval_id)


__all__ = ["raise_turn_ceiling_approval"]
