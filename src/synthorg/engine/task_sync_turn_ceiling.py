"""Ask a human whether a run that ran out of turns should carry on.

A run parks here with its work intact and its workspace preserved, which is
the whole point of parking rather than failing. But a park nobody can answer
is a quieter deadlock than the failure it replaces: a clarification or a
decision fork arrives with an approval the agent already raised through its
tool, and a spent turn budget has no such author.

So the question is armed BEFORE the task moves, and both halves have to land:
a ``ParkedContext`` for the resume path to restore, and an approval carrying
``ApprovalSource.PARKED_CONTEXT`` so the decision router sends it there. An
approval on any other source falls through to the review gate, which expects a
task in review and finds one awaiting input. If either half cannot be written
the run does not park at all: it ends ``MAX_TURNS``, which is retryable and
visible to the stall derivation, rather than sitting in ``AWAITING_INPUT``
with nothing that can move it.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource
from synthorg.approval.models import EscalationInfo
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.loop_turn_budget import TURN_CEILING_METADATA_KEY
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_CREATED,
    APPROVAL_GATE_REVIEW_STORE_FAILED,
)

logger = get_logger(__name__)

#: Its own action type, not the review family's: an autonomy grant written
#: for "review this deliverable" must not silently also mean "spend another
#: four turn budgets on it".
TURN_CEILING_ACTION_TYPE = "execution:extend_turns"


def _is_turn_ceiling_park(result: ExecutionResult) -> bool:
    """Report whether *result* is a run parked for a spent turn budget.

    Returns:
        ``True`` for a PARKED result carrying the turn-ceiling marker.
    """
    return (
        result.termination_reason is TerminationReason.PARKED
        and result.metadata.get(TURN_CEILING_METADATA_KEY) is True
    )


async def _discard_parked(
    approval_gate: ApprovalGate,
    approval_id: UUID,
    *,
    task_id: str,
) -> None:
    """Drop a parked context whose approval never landed.

    ``resume_context`` loads and deletes, which is the only delete the gate
    offers; the loaded value is discarded because there is nothing left to
    resume into. Best-effort: this already runs on a failure path, and a
    second one must not replace the ceiling's own outcome.

    Args:
        approval_gate: Gate holding the parked context.
        approval_id: The park's identifier.
        task_id: Task the park belongs to, for the failure log.
    """
    try:
        await approval_gate.resume_context(str(approval_id), session_id=task_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- cleanup on an already-failed path
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_REVIEW_STORE_FAILED,
            approval_id=str(approval_id),
            task_id=task_id,
            note="orphaned parked context could not be discarded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _downgrade_to_max_turns(result: ExecutionResult) -> ExecutionResult:
    """Turn an unarmable park back into the ordinary ceiling failure.

    Returns:
        The result as ``MAX_TURNS``, with the park marker removed so no
        later stage treats it as a park.
    """
    metadata = {
        key: value
        for key, value in result.metadata.items()
        if key != TURN_CEILING_METADATA_KEY
    }
    return result.model_copy(
        update={
            "termination_reason": TerminationReason.MAX_TURNS,
            "metadata": metadata,
        }
    )


def _ceiling_question(
    ctx: AgentContext,
    *,
    agent_id: str,
    task_id: str,
    approval_id: UUID,
) -> tuple[EscalationInfo, ApprovalItem]:
    """Compose what the operator is asked when a run runs out of turns.

    Separate from arming it because the two answer different questions: this
    one is what the queue and the resume router read, and the caller's job is
    getting both halves durable or neither.

    Args:
        ctx: The context of the run that reached its ceiling.
        agent_id: Agent whose run parked.
        task_id: Task the run was working on.
        approval_id: The identifier both halves are keyed by.

    Returns:
        The escalation the parked context is filed under, and the approval
        item the operator answers.
    """
    task_execution = ctx.task_execution
    title = task_execution.task.title if task_execution is not None else task_id
    escalation = EscalationInfo(
        approval_id=str(approval_id),
        tool_call_id=f"turn-ceiling-{task_id}",
        tool_name="turn_budget",
        action_type=TURN_CEILING_ACTION_TYPE,
        risk_level=ApprovalRiskLevel.MEDIUM,
        reason=f"Run reached its turn ceiling after {ctx.turn_count} turns",
    )
    item = ApprovalItem(
        id=approval_id,
        action_type=TURN_CEILING_ACTION_TYPE,
        title=f"Out of turns: {title}",
        description=(
            f"Agent {agent_id} used {ctx.turn_count} turns across "
            f"{ctx.turn_extensions_granted} extension(s) without finishing "
            "the task. Its workspace and everything it wrote are intact. "
            "Approve to carry on, reject to stop here."
        ),
        requested_by=agent_id,
        risk_level=ApprovalRiskLevel.MEDIUM,
        # The resume router keys on this and nothing else: any other source
        # falls through to the review gate and the run is never restored.
        source=ApprovalSource.PARKED_CONTEXT,
        created_at=datetime.now(UTC),
        task_id=task_id,
        metadata={TURN_CEILING_METADATA_KEY: "true"},
    )
    return escalation, item


async def arm_turn_ceiling_park(
    result: ExecutionResult,
    *,
    agent_id: str,
    task_id: str,
    approval_store: ApprovalStoreProtocol | None,
    approval_gate: ApprovalGate | None,
) -> ExecutionResult:
    """Make a turn-ceiling park answerable, or refuse to park at all.

    Args:
        result: The terminal result of the run that reached its ceiling.
        agent_id: Agent whose run parked.
        task_id: Task the run was working on.
        approval_store: Store the question is raised in.
        approval_gate: Gate that persists the context the resume restores.

    Returns:
        *result* unchanged once both halves are durable, or the same run
        as ``MAX_TURNS`` when either half could not be written.
    """
    if not _is_turn_ceiling_park(result):
        return result
    if approval_store is None or approval_gate is None:
        logger.warning(
            APPROVAL_GATE_REVIEW_STORE_FAILED,
            task_id=task_id,
            agent_id=agent_id,
            note="no approval store or gate; ceiling ends the run instead",
        )
        return _downgrade_to_max_turns(result)

    ctx = result.context
    approval_id = uuid4()
    escalation, item = _ceiling_question(
        ctx,
        agent_id=agent_id,
        task_id=task_id,
        approval_id=approval_id,
    )
    parked = False
    try:
        await approval_gate.park_context(
            escalation=escalation,
            context=ctx,
            agent_id=agent_id,
            task_id=task_id,
            session_id=task_id,
        )
        parked = True
        await approval_store.add(item)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- an unarmable park downgrades to MAX_TURNS,
        # which is the visible, retryable outcome; raising here would lose the
        # run's work as well as its question.
        reraise_critical(exc)
        if parked:
            # The context landed and the approval did not, so nothing will
            # ever resume it: a stored context no queue entry names is
            # unreachable by every route and grows the parked table forever.
            await _discard_parked(approval_gate, approval_id, task_id=task_id)
        logger.warning(
            APPROVAL_GATE_REVIEW_STORE_FAILED,
            approval_id=str(approval_id),
            task_id=task_id,
            agent_id=agent_id,
            note="turn-ceiling park not armed; ceiling ends the run instead",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _downgrade_to_max_turns(result)
    logger.info(
        APPROVAL_GATE_REVIEW_CREATED,
        approval_id=str(approval_id),
        task_id=task_id,
        agent_id=agent_id,
        action_type=TURN_CEILING_ACTION_TYPE,
    )
    return result


__all__ = ["TURN_CEILING_ACTION_TYPE", "arm_turn_ceiling_park"]
