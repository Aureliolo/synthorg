"""One corrective turn for a work run that has delivered nothing yet.

The zero-artifact guard is right that a silent no-op is a failure, but on the
first empty turn it ends a run whose remaining turns were never used, and does
so before the agent has been told what it missed. Exactly one correction is
issued here, so a run can only fail for delivering nothing after being told it
delivered nothing.

Whether it HAS delivered anything is asked of the workspace. A recorded leaf
ran ``pwd``, ``ls``, ``cat``, ``python3 --version`` and ``mkdir``, announced
what it would write next, and was read as finished on turn 6 of 40 with an
empty tree: every one of those calls counted as delivery under a proxy that
only knows tool names, so the correction never fired. A tool name cannot
answer this, because the same tool writes a file or lists a directory
depending on its arguments.
"""

from collections.abc import Iterable

from synthorg.engine.artifacts.baseline_scope import (
    current_run_baseline,
    produced_nothing_since,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_EMPTY_RUN_NUDGED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.tools.discovery import DISCOVERY_NAMES

logger = get_logger(__name__)


async def delivered_nothing(turns: Iterable[TurnRecord]) -> bool:
    """Report whether this run has produced anything yet.

    The single owner of that question inside the loop, asked by the
    correction below and by the loop's own no-op classification, so the two
    cannot disagree about a run one of them is about to end.

    The workspace answers it whenever a baseline was taken, and a baseline is
    taken for exactly the runs that declare deliverables. Falling back to the
    tool-call proxy is the unwired case only: it is the weaker evidence, and
    an absent baseline must not change what a run is told.

    Returns:
        ``True`` when nothing has been produced.
    """
    unchanged = await produced_nothing_since(current_run_baseline())
    if unchanged is not None:
        return unchanged
    return _called_no_delivering_tool(turns)


def _called_no_delivering_tool(turns: Iterable[TurnRecord]) -> bool:
    """Report whether no tool call so far could have produced a deliverable.

    The fallback for a run with no baseline to compare against. "Called a
    tool" is the proxy, and the discovery tools break it: they exist to
    describe the other tools and return nothing else, so a run that asks what
    tools it has and then answers in prose has delivered exactly as little as
    one that called nothing at all, while passing a guard that counts calls.

    The set is deliberately the discovery tools alone rather than every
    read-only tool. Reading a file delivers nothing either, but nothing here
    declares which tools mutate, and a guess at that boundary would be a
    classification invented at the guard rather than declared at the tool.
    That is precisely the limit the workspace question above removes, and it
    is why this is the fallback rather than the answer: ``mkdir`` and ``ls``
    are the same call to a name.

    Returns:
        ``True`` when every tool call so far was a discovery call, or there
        were none.
    """
    return all(
        name in DISCOVERY_NAMES for turn in turns for name in turn.tool_calls_made
    )


async def _nudge_message(
    ctx: AgentContext,
    turns: list[TurnRecord],
) -> ChatMessage | None:
    """Decide whether this empty turn earns a correction, and word it.

    Fired at most once, on the first empty turn, and only when correcting is
    possible and warranted:

    * the run is not a resumed segment, whose earlier segments may already
      have delivered;
    * the task declares artifacts, so there is a concrete deliverable to name;
    * this is the first empty turn, so the correction fires once;
    * the workspace holds nothing this run put there;
    * a turn remains to correct in.

    Keyed on the first *empty* turn rather than on the first turn, because
    those stopped being the same thing once a discovery call counted as
    neither delivery nor silence: a run that asks what tools it has and then
    answers in prose has produced nothing on its second turn, which is exactly
    the run this correction is for.

    A second empty turn falls through to the zero-artifact guard, so the
    correction costs one round trip and never loops.

    Args:
        ctx: The context of the run that produced an empty turn.
        turns: Every turn recorded so far, this one included.

    Returns:
        The corrective user message, or ``None`` when no nudge applies.
    """
    if is_resumed_run():
        return None
    execution = ctx.task_execution
    if execution is None or not execution.task.artifacts_expected:
        return None
    empty_so_far = sum(1 for turn in turns if not turn.tool_calls_made)
    if empty_so_far != 1 or ctx.max_turns <= len(turns):
        return None
    if not await delivered_nothing(turns):
        return None
    # Fenced, because a declared path is model-authored: decomposition takes
    # it from the LLM as a non-blank string with no charset restriction, so a
    # path spelling its own instructions would otherwise arrive inside a
    # sentence telling the agent to act on it now. The sibling that renders
    # the same field for the evaluate brief fences it the same way.
    declared = wrap_untrusted(
        TAG_TASK_DATA,
        ", ".join(artifact.path for artifact in execution.task.artifacts_expected),
    )
    return ChatMessage(
        role=MessageRole.USER,
        content=(
            "Your workspace is exactly as you found it, so this task has "
            "produced nothing: listing, reading and creating directories "
            "leave no deliverable behind. It declares these deliverables: "
            f"{declared} Prose is not a deliverable. Use your tools to "
            "create them now, then say you are done."
        ),
    )


async def nudge_empty_run(
    ctx: AgentContext,
    turns: list[TurnRecord],
    turn_number: int,
) -> AgentContext | None:
    """Extend *ctx* with a correction, when an empty turn earns one.

    Args:
        ctx: The context of the run that produced an empty turn.
        turns: Every turn recorded so far, this one included.
        turn_number: The 1-based number of the turn that came back empty.

    Returns:
        The context to run the corrective turn with, or ``None`` to let the
        caller treat the empty turn as the run's answer.
    """
    message = await _nudge_message(ctx, turns)
    if message is None:
        return None
    logger.info(
        EXECUTION_LOOP_EMPTY_RUN_NUDGED,
        execution_id=ctx.execution_id,
        turn=turn_number,
        turns_remaining=ctx.max_turns - turn_number,
    )
    return ctx.with_message(message)
