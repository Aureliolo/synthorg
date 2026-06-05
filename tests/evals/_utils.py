"""Utilities for agent evaluation testing.

Provides ``n1_prefix_replay`` for regression testing multi-turn
agent behavior by replaying the first N-1 turns of a recorded
trace and letting the agent generate only the final turn.
"""

import inspect
from collections.abc import Awaitable, Callable

from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.execution.turn import TurnRecord


async def n1_prefix_replay(
    trace: ExecutionResult,
    *,
    replay_fn: Callable[
        [tuple[TurnRecord, ...]],
        TurnRecord | Awaitable[TurnRecord],
    ],
) -> TurnRecord:
    """Replay first N-1 turns, let agent generate turn N only.

    Avoids compounding errors when evaluating multi-turn behaviors:
    the agent receives the exact context from a real execution up
    to turn N-1, and is evaluated only on its generation of turn N.

    Args:
        trace: Complete execution result with N turns.
        replay_fn: Callable that takes the prefix turns (0..N-2)
            and returns the agent's generated final turn.  May be
            sync or async.

    Returns:
        The agent's generated final turn, for comparison against
        ``trace.turns[-1]``.

    Raises:
        ValueError: If trace has fewer than 2 turns.
    """
    if len(trace.turns) < 2:
        msg = f"Trace must have >= 2 turns for N-1 replay, got {len(trace.turns)}"
        raise ValueError(msg)

    prefix = trace.turns[:-1]
    result = replay_fn(prefix)
    if inspect.isawaitable(result):
        return await result
    return result
