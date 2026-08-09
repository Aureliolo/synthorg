"""What happens when a run reaches its turn ceiling.

Reaching the ceiling used to end the run: the task was failed, its workspace
was torn down, and everything the agent had written was discarded. In a live
run four of five build agents ended that way, each having produced real
files. A ceiling is a backstop against a pathological loop, not a verdict on
work that is simply taking longer than the estimate.

So the run first grants itself another budget, a bounded number of times, and
only parks for a human once those are spent. Parking preserves the workspace
and asks a question; it never throws the work away.
"""

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_CEILING_PARKED,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURNS_EXTENDED,
)

logger = get_logger(__name__)

#: Marks a park as "this run ran out of turn budget", so the sync layer can
#: tell it from a clarification or a decision fork and ask the matching
#: question. Read by ``engine.task_sync``.
TURN_CEILING_METADATA_KEY = "turn_ceiling"


def grant_extension(ctx: AgentContext) -> AgentContext | None:
    """Give the run another turn budget, while it has extensions left.

    Each extension is worth the run's original budget again, so the headroom
    granted scales with whatever the operator configured rather than with a
    second number nobody tuned.

    Args:
        ctx: The context of a run that has just reached its ceiling.

    Returns:
        A context carrying further headroom and one fewer extension, or
        ``None`` when the extensions are spent and the run must park.
    """
    if ctx.turn_extensions_remaining <= 0:
        return None
    granted = ctx.turn_extensions_granted + 1
    extended = ctx.model_copy(
        update={
            "max_turns": ctx.max_turns + ctx.max_turns // granted,
            "turn_extensions_remaining": ctx.turn_extensions_remaining - 1,
            "turn_extensions_granted": granted,
        }
    )
    logger.info(
        EXECUTION_LOOP_TURNS_EXTENDED,
        execution_id=ctx.execution_id,
        turns_used=ctx.turn_count,
        new_max_turns=extended.max_turns,
        extensions_remaining=extended.turn_extensions_remaining,
    )
    return extended


def ceiling_result(
    ctx: AgentContext,
    turns: list[TurnRecord],
) -> ExecutionResult:
    """Build the result for a run whose turn budget is finally spent.

    ``PARKED`` rather than ``MAX_TURNS`` whenever the run actually took its
    extensions: it has not failed, it has run out of budget with its work
    intact, and the honest next step is to ask whether to carry on. The sync
    layer reads :data:`TURN_CEILING_METADATA_KEY` to move the task to
    ``AWAITING_INPUT`` and raise that question, and the workspace survives
    because a parked agent's workspace is never torn down.

    An operator who sets the extension budget to zero is asking for the old
    behaviour, and gets it: the first ceiling ends the run.

    Args:
        ctx: The context of the run that reached its ceiling.
        turns: Every turn the run recorded.

    Returns:
        The run's terminal :class:`ExecutionResult`.
    """
    if ctx.turn_extensions_granted == 0:
        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.MAX_TURNS.value,
            turns=len(turns),
        )
        return ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.MAX_TURNS,
            turns=tuple(turns),
        )
    logger.info(
        EXECUTION_LOOP_CEILING_PARKED,
        execution_id=ctx.execution_id,
        turns=len(turns),
        max_turns=ctx.max_turns,
        extensions_granted=ctx.turn_extensions_granted,
    )
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.PARKED,
        turns=tuple(turns),
        metadata={TURN_CEILING_METADATA_KEY: True},
    )


__all__ = ["TURN_CEILING_METADATA_KEY", "ceiling_result", "grant_extension"]
