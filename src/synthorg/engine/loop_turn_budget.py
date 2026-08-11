"""What happens when a run reaches its turn ceiling.

A ceiling is a backstop against a pathological loop, not a verdict on work
that is simply taking longer than the estimate. Ending the run there fails
the task, tears down the workspace and discards everything the agent wrote,
which is the wrong answer for the agent that is nearly finished and the right
one only for the agent going in circles.

So the run grants itself another budget, a bounded number of times and only
while it is still doing something, and parks for a human once those are
spent. Parking preserves the workspace and asks a question; it never throws
the work away. An extension is earned rather than automatic, so the run that
is going in circles still stops at its first ceiling.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import DEFAULT_MAX_TURN_EXTENSIONS, AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_LOOP_CEILING_PARKED,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURNS_EXTENDED,
    EXECUTION_MAX_TURNS_EXCEEDED,
)
from synthorg.observability.redaction import safe_error_description
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Marks a park as "this run ran out of turn budget", so the sync layer can
#: tell it from a clarification or a decision fork and ask the matching
#: question. Read by ``engine.task_sync``.
TURN_CEILING_METADATA_KEY = "turn_ceiling"


async def resolve_turn_extensions(
    config_resolver: ConfigResolverProtocol | None,
    *,
    agent_id: str,
    task_id: str,
) -> int:
    """Resolve how many further turn budgets a run may take.

    One owner for the read, because two paths need the answer: a fresh run
    sizing its context, and a resumed one being given somewhere to continue.
    Read per call, beside the cap it extends, so raising either takes effect
    on the next dispatch rather than the next restart.

    Args:
        config_resolver: Settings resolver, or ``None`` when unwired.
        agent_id: Agent the run belongs to, for the failure log.
        task_id: Task the run belongs to, for the failure log.

    Returns:
        The operator-configured ``engine.max_turn_extensions``, else
        :data:`DEFAULT_MAX_TURN_EXTENSIONS`. Zero is a legitimate choice
        (end the run at the first ceiling) so it is honoured rather than
        read as unset; a settings-backend outage fails safe.
    """
    if config_resolver is None:
        return DEFAULT_MAX_TURN_EXTENSIONS
    try:
        resolved = await config_resolver.get_int("engine", "max_turn_extensions")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-default wiring
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            note="failed to read engine.max_turn_extensions, using default",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_MAX_TURN_EXTENSIONS
    return resolved if resolved >= 0 else DEFAULT_MAX_TURN_EXTENSIONS


def _budget_size(ctx: AgentContext) -> int:
    """Return the budget one extension is worth.

    The ceiling grows by one configured budget per extension taken, so
    dividing by the extensions taken recovers the budget the operator set,
    whatever it was.

    Returns:
        The per-extension turn budget.
    """
    return ctx.max_turns // (ctx.turn_extensions_granted + 1)


def grant_extension(
    ctx: AgentContext,
    turns: list[TurnRecord],
) -> AgentContext | None:
    """Give the run another turn budget, if it earned one and has one left.

    Each extension is worth the run's original budget again, so the headroom
    granted scales with whatever the operator configured rather than with a
    second number nobody tuned. It is granted only to a run that called a
    tool in the budget it just spent: a run with the default allowance can
    reach four times the configured ceiling, and the difference between that
    being a rescue and being a runaway is whether the turns were doing
    anything.

    Args:
        ctx: The context of a run that has just reached its ceiling.
        turns: Every turn the run recorded.

    Returns:
        A context carrying further headroom and one fewer extension, or
        ``None`` when the extensions are spent, or when the budget just
        spent produced nothing, and the run must stop.
    """
    if ctx.turn_extensions_remaining <= 0:
        return None
    if not any(turn.tool_calls_made for turn in turns[-_budget_size(ctx) :]):
        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.MAX_TURNS.value,
            turns=len(turns),
            note="no tool call in the budget just spent; extension not granted",
        )
        return None
    granted = ctx.turn_extensions_granted + 1
    extended = ctx.model_copy(
        update={
            "max_turns": ctx.max_turns + _budget_size(ctx),
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


def restore_turn_budget(
    ctx: AgentContext,
    *,
    approved: bool,
    extensions: int,
) -> AgentContext:
    """Give a resumed run somewhere to run, or leave it alone.

    A context restored from a park has whatever budget it parked with. For
    every park but this one that is turns to spare; for a run that parked
    because it ran out, resuming into a spent budget means reaching the
    ceiling again on re-entry and asking the same question, which is a loop
    the human cannot break by answering.

    So a run with nothing left is handed one more budget of the size the
    operator configured, and the decision sets what happens after it:
    approving restores the extension allowance too, so the run can carry on
    the way it did before and ask again if it needs to; rejecting leaves it
    at zero, so the next ceiling ends the run instead of re-asking. Either
    way the resumed run terminates.

    Args:
        ctx: The restored context.
        approved: Whether the human approved carrying on.
        extensions: The operator's configured extension allowance.

    Returns:
        *ctx* unchanged when it still has turns, else a context with one
        further budget and the decision's extension allowance.
    """
    if ctx.turn_count < ctx.max_turns:
        return ctx
    granted = ctx.turn_extensions_granted
    budget = _budget_size(ctx)
    logger.info(
        EXECUTION_LOOP_TURNS_EXTENDED,
        execution_id=ctx.execution_id,
        turns_used=ctx.turn_count,
        new_max_turns=ctx.max_turns + budget,
        extensions_remaining=extensions if approved else 0,
    )
    return ctx.model_copy(
        update={
            "max_turns": ctx.max_turns + budget,
            "turn_extensions_remaining": extensions if approved else 0,
            # Zero on a rejection so the next ceiling ends the run rather
            # than parking it: ``ceiling_result`` parks only a run that
            # took an extension, and a rejected one may take no more.
            "turn_extensions_granted": granted + 1 if approved else 0,
        }
    )


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
    # The scorers key on this event name, and its only other emitter is
    # ``AgentContext.with_turn_completed``'s guard, which a loop that checks
    # ``has_turns_remaining`` before spending a turn never reaches. Both
    # outcomes below burned the whole budget, so both say so here and the
    # branch below reports what was done about it.
    logger.warning(
        EXECUTION_MAX_TURNS_EXCEEDED,
        execution_id=ctx.execution_id,
        max_turns=ctx.max_turns,
        turn_count=ctx.turn_count,
        extensions_granted=ctx.turn_extensions_granted,
    )
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


__all__ = [
    "TURN_CEILING_METADATA_KEY",
    "ceiling_result",
    "grant_extension",
    "restore_turn_budget",
]
