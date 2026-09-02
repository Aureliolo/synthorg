"""The arithmetic behind ``AgentContext``'s heavier state transitions.

Each function answers with the ``model_copy`` update one transition applies,
and refuses the transitions the context cannot make (a turn past the cap, a
task hop with no task). Kept beside the model rather than inside it for the
same reason the disclosure and background-job updates are: the model names
the transitions, and what each one computes is its own concern.
"""

from typing import TYPE_CHECKING

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.compaction.models import CompressionMetadata
from synthorg.engine.errors import ExecutionStateError, MaxTurnsExceededError
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_NO_TASK,
    EXECUTION_CONTEXT_TRANSITION_FAILED,
    EXECUTION_MAX_TURNS_EXCEEDED,
)
from synthorg.providers.models import ChatMessage, TokenUsage, add_token_usage

if TYPE_CHECKING:
    from synthorg.engine.context import AgentContext

logger = get_logger(__name__)


def turn_completed_update(
    ctx: AgentContext,
    usage: TokenUsage,
    response_msg: ChatMessage | None,
) -> dict[str, object]:
    """The update that records one completed turn on *ctx*.

    The turn count and the cost advance whether or not there is a message: a
    wordless turn still happened and was still billed.

    Returns:
        The fields to replace.

    Raises:
        MaxTurnsExceededError: If ``max_turns`` has been reached.
    """
    if not ctx.has_turns_remaining:
        msg = (
            f"Agent {ctx.identity.id} exceeded max_turns "
            f"({ctx.max_turns}) for execution {ctx.execution_id}"
        )
        logger.error(
            EXECUTION_MAX_TURNS_EXCEEDED,
            execution_id=ctx.execution_id,
            agent_id=str(ctx.identity.id),
            max_turns=ctx.max_turns,
            turn_count=ctx.turn_count,
        )
        raise MaxTurnsExceededError(msg)
    conversation = (
        ctx.conversation if response_msg is None else (*ctx.conversation, response_msg)
    )
    updates: dict[str, object] = {
        "turn_count": ctx.turn_count + 1,
        "conversation": conversation,
        "accumulated_cost": add_token_usage(ctx.accumulated_cost, usage),
    }
    if ctx.task_execution is not None:
        updates["task_execution"] = ctx.task_execution.with_cost(usage)
    return updates


def compression_update(
    *,
    metadata: CompressionMetadata,
    compressed_conversation: tuple[ChatMessage, ...],
    fill_tokens: int,
    pinned: frozenset[int],
) -> dict[str, object]:
    """The update that replaces a conversation with its compressed form.

    Returns:
        The fields to replace.

    Raises:
        ValueError: If ``fill_tokens`` is negative, or a pin falls outside
            the compressed conversation.
    """
    if fill_tokens < 0:
        msg = f"fill_tokens must be >= 0, got {fill_tokens}"
        raise ValueError(msg)
    out_of_range = sorted(
        i for i in pinned if not 0 <= i < len(compressed_conversation)
    )
    if out_of_range:
        msg = (
            f"pinned indices outside the compressed conversation "
            f"({len(compressed_conversation)} messages): {out_of_range}"
        )
        raise ValueError(msg)
    return {
        "conversation": compressed_conversation,
        "compression_metadata": metadata,
        "context_fill_tokens": fill_tokens,
        "pinned_message_indices": pinned,
    }


def task_transition_update(
    ctx: AgentContext,
    target: TaskStatus,
    *,
    reason: str,
) -> dict[str, object]:
    """The update that moves *ctx*'s task execution to *target*.

    Returns:
        The fields to replace.

    Raises:
        ExecutionStateError: If no task execution is set.
        ValueError: If the transition is invalid (from
            ``validate_transition``).
    """
    if ctx.task_execution is None:
        msg = "Cannot transition task status: no task execution is set"
        logger.error(
            EXECUTION_CONTEXT_NO_TASK,
            execution_id=ctx.execution_id,
            agent_id=str(ctx.identity.id),
            target_status=target.value,
        )
        raise ExecutionStateError(msg)
    try:
        new_execution = ctx.task_execution.with_transition(target, reason=reason)
    except ValueError:
        logger.warning(
            EXECUTION_CONTEXT_TRANSITION_FAILED,
            execution_id=ctx.execution_id,
            agent_id=str(ctx.identity.id),
            target_status=target.value,
            current_status=ctx.task_execution.status.value,
        )
        raise
    return {"task_execution": new_execution}


__all__ = ["compression_update", "task_transition_update", "turn_completed_update"]
