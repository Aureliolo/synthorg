"""How a wave's outcome is read: did it fail, and who is still waiting.

The single owner of both questions, shared by every dispatcher, because two
copies of the first are how two dispatchers came to disagree about it.
"""

from collections.abc import Iterable

from synthorg.engine.coordination.models import CoordinationWave
from synthorg.engine.parallel_models import ParallelExecutionResult


def parked_tasks(waves: Iterable[CoordinationWave]) -> frozenset[str]:
    """Task ids whose run parked awaiting a human decision.

    Args:
        waves: The waves executed so far.

    Returns:
        The set of task ids still waiting on an operator. A wave with no
        execution result contributes nothing: it never ran, so it parked
        nothing.
    """
    return frozenset(
        outcome.task_id
        for wave in waves
        if wave.execution_result is not None
        for outcome in wave.execution_result.outcomes
        if outcome.is_awaiting_human
    )


def classify_wave(
    wave_idx: int,
    exec_result: ParallelExecutionResult,
) -> tuple[bool, str | None]:
    """Decide whether a wave failed, and say why when it did.

    A wave fails when an agent genuinely failed. An agent parked on an
    escalation is waiting on a human: the wave is unfinished, not failed, and
    failing it would skip the merge, tear down the workspace the resume needs,
    and kill the plan while the approval is still open.

    Args:
        wave_idx: Index of the wave, for the error message.
        exec_result: The wave's parallel-execution result.

    Returns:
        ``(success, error)``: ``success`` is False only when an agent failed;
        ``error`` names the counts when it did, else ``None``.
    """
    if not exec_result.any_failed:
        return True, None
    parked = exec_result.agents_awaiting_human
    suffix = f", {parked} awaiting a human" if parked else ""
    return (
        False,
        f"Wave {wave_idx}: {exec_result.agents_failed} agent(s) failed{suffix}",
    )
