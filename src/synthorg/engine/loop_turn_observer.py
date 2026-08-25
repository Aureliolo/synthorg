"""Turn-observer notification for the execution loop.

One of the ``loop_*`` helpers the loop delegates to. Kept out of the loop
body because notifying an observer is the loop's only purely observational
side effect, and its failure handling (swallow everything except
cancellation) reads as a policy rather than as a step of the algorithm.
"""

import asyncio

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TurnObserver, TurnProgress
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import EXECUTION_TURN_OBSERVER_FAILED
from synthorg.providers.models import CompletionResponse

logger = get_logger(__name__)


async def notify_turn_observer(
    turn_number: int,
    response: CompletionResponse,
    observer: TurnObserver | None,
    ctx: AgentContext,
) -> None:
    """Fire the optional turn observer with this turn's progress.

    Purely observational: an observer failure is logged and swallowed so it
    can never corrupt the run, but cancellation still propagates so a client
    disconnect tears a streamed action down at once.

    Raises:
        CancelledError: Propagated so a client disconnect halts the run.
    """
    if observer is None:
        return
    tool_names = tuple(call.name for call in response.tool_calls)
    try:
        await observer(TurnProgress(turn_number, tool_names, ctx))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort observer
        reraise_critical(exc)
        logger.warning(
            EXECUTION_TURN_OBSERVER_FAILED,
            turn_number=turn_number,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["notify_turn_observer"]
