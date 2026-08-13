# module-kind: code
"""Stopping a run that keeps asking for tools nobody has registered.

The turn ceiling is the wrong instrument for this. It measures how long a run
has gone on, and a run guessing at tool names goes on for the whole budget
before anything notices; the extension guard then denies it a second budget,
which is right but late. This is the early stop, and it is decidable from one
fact the loop already records: whether the tools a turn asked for exist.

Kept apart from the stagnation detector on purpose. That detector asks whether
the same call is being repeated, keying on ``name:args_hash``, and a live run
defeated it by drifting its arguments a few characters every turn while asking
for the same non-existent tool 246 times. Argument identity is the wrong
question here: a call that resolves to nothing made no progress whatever its
arguments were, and the registry has already answered it by name with its
nearest matches.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_budget_defaults import DEFAULT_MAX_UNRESOLVED_TOOL_TURNS
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_LOOP_TERMINATED,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Which tool names the stopped run kept asking for, and how many consecutive
#: turns it spent on them. Named keys rather than prose, so the sync layer and
#: the scorers can read the finding rather than parse a sentence.
UNRESOLVED_TOOLS_METADATA_KEY: Final[str] = "unresolved_tools"
UNRESOLVED_TURNS_METADATA_KEY: Final[str] = "unresolved_turns"


async def resolve_max_unresolved_tool_turns(
    config_resolver: ConfigResolverProtocol | None,
    *,
    agent_id: str,
    task_id: str,
) -> int:
    """Resolve how many consecutive no-tool turns a run may spend.

    Read per run, beside the turn budget it complements, so raising or
    disabling it takes effect on the next dispatch rather than the next
    restart.

    Args:
        config_resolver: Settings resolver, or ``None`` when unwired.
        agent_id: Agent the run belongs to, for the failure log.
        task_id: Task the run belongs to, for the failure log.

    Returns:
        The operator-configured ``engine.max_unresolved_tool_turns``, else
        :data:`DEFAULT_MAX_UNRESOLVED_TOOL_TURNS`. Zero is a legitimate
        choice (never stop early) so it is honoured rather than read as
        unset; a settings-backend outage fails safe.
    """
    if config_resolver is None:
        return DEFAULT_MAX_UNRESOLVED_TOOL_TURNS
    try:
        resolved = await config_resolver.get_int("engine", "max_unresolved_tool_turns")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-default wiring
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            note="failed to read engine.max_unresolved_tool_turns, using default",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_MAX_UNRESOLVED_TOOL_TURNS
    return resolved if resolved >= 0 else DEFAULT_MAX_UNRESOLVED_TOOL_TURNS


def unresolved_streak(turns: list[TurnRecord]) -> int:
    """Count the trailing turns that asked for tools and ran none.

    A turn that called no tool at all breaks the streak rather than extending
    it: that is a thinking turn or a finished run, and neither is this.

    Returns:
        How many consecutive most-recent turns asked for at least one tool and
        resolved none of them.
    """
    streak = 0
    for turn in reversed(turns):
        if not turn.tool_calls_made or turn.resolved_tool_calls:
            break
        streak += 1
    return streak


def unresolved_tools_result(
    ctx: AgentContext,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Stop a run whose recent turns all asked for tools that do not exist.

    Args:
        ctx: The run's context, carrying the operator's ceiling.
        turns: Every turn the run has recorded.

    Returns:
        A ``STAGNATION`` result when the streak reaches the ceiling, else
        ``None``. ``STAGNATION`` rather than a reason of its own because that
        is what this is, and the sync layer already knows what to do with it.
    """
    limit = ctx.max_unresolved_tool_turns
    if limit <= 0:
        return None
    streak = unresolved_streak(turns)
    if streak < limit:
        return None
    # The names, because the whole finding is which tool it kept asking for;
    # the registry answered by name every time and was not heard.
    asked = sorted(set(turns[-1].tool_calls_made))
    logger.warning(
        EXECUTION_LOOP_TERMINATED,
        execution_id=ctx.execution_id,
        reason=TerminationReason.STAGNATION.value,
        turns=len(turns),
        unresolved_turns=streak,
        tools=asked,
    )
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.STAGNATION,
        turns=tuple(turns),
        # Carried as metadata, not error_message: the result model reserves
        # that for TerminationReason.ERROR, and this is not an error, it is a
        # run stopped for asking after nothing.
        metadata={
            UNRESOLVED_TOOLS_METADATA_KEY: asked,
            UNRESOLVED_TURNS_METADATA_KEY: streak,
        },
    )


__all__ = [
    "resolve_max_unresolved_tool_turns",
    "unresolved_streak",
    "unresolved_tools_result",
]
