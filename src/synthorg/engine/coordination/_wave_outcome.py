"""How a wave's outcome is read: did it fail, and who is still waiting.

The single owner of both questions, shared by every dispatcher, because two
copies of the first are how two dispatchers came to disagree about it.
"""

from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.engine.coordination.models import CoordinationWave
from synthorg.engine.parallel_models import ParallelExecutionResult


class WaveVerdict(BaseModel):
    """What a wave's result means for the waves after it.

    Failing and waiting are different things and the dispatchers need both:
    a failed wave skips its merge, while a wave with someone waiting on a
    human is unfinished, keeps its work, and must not be built on yet.

    Attributes:
        failed: Whether an agent in the wave genuinely failed.
        parked_task_ids: Tasks whose run is waiting on an operator.
        error: Why the wave failed, present exactly when it did.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    failed: bool = Field(description="An agent in the wave failed")
    parked_task_ids: frozenset[str] = Field(
        default=frozenset(),
        description="Tasks waiting on an operator decision",
    )
    error: str | None = Field(
        default=None,
        description="Why the wave failed; None when it did not",
    )

    @model_validator(mode="after")
    def _error_accompanies_failure(self) -> Self:
        """Keep the reason and the verdict from drifting apart.

        Returns:
            The validated verdict.

        Raises:
            ValueError: When a failure carries no reason, or a
                non-failure carries one.
        """
        if self.failed is (self.error is None):
            msg = "a failed wave names its error and a passing wave carries none"
            raise ValueError(msg)
        return self

    @computed_field
    @property
    def success(self) -> bool:
        """Whether the wave is free of failures."""
        return not self.failed

    def blocks_dependents(self, *, fail_fast: bool) -> bool:
        """Whether the waves after this one must not run.

        Waves are dependency levels, so everything after this one was
        scheduled on the promise that this one finished. A park has not
        finished: its work is mid-flight in a workspace a human has yet to
        release, and a dependent wave started now reads a half-written
        result as its input. So a park blocks whatever the caller asked for.

        A failure is the caller's call, which is what *fail_fast* is: a run
        that tolerates failures proceeds on what did land. That is why this
        takes the flag rather than reading only the verdict; splitting the
        two halves across the verdict and its caller is how they drift.

        Args:
            fail_fast: Whether the run stops at the first failed wave.

        Returns:
            ``True`` when the next wave must not start.
        """
        return bool(self.parked_task_ids) or (self.failed and fail_fast)


def phase_name(wave_idx: int) -> str:
    """Return the phase label a wave reports under.

    Args:
        wave_idx: Which wave of the dispatch this is.

    Returns:
        The ``execute_wave_<n>`` label.
    """
    return f"execute_wave_{wave_idx}"


def parked_in_result(exec_result: ParallelExecutionResult) -> frozenset[str]:
    """Task ids in one wave's result whose run parked for a human.

    Args:
        exec_result: One wave's parallel-execution result.

    Returns:
        The set of task ids waiting on an operator.
    """
    return frozenset(
        outcome.task_id for outcome in exec_result.outcomes if outcome.is_awaiting_human
    )


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
        task_id
        for wave in waves
        if wave.execution_result is not None
        for task_id in parked_in_result(wave.execution_result)
    )


def classify_wave(
    wave_idx: int,
    exec_result: ParallelExecutionResult,
) -> WaveVerdict:
    """Decide what a wave's result means, and say why when it failed.

    A wave fails when an agent genuinely failed. An agent parked on an
    escalation is waiting on a human: the wave is unfinished, not failed, and
    failing it would skip the merge, tear down the workspace the resume needs,
    and kill the plan while the approval is still open.

    Args:
        wave_idx: Index of the wave, for the error message.
        exec_result: The wave's parallel-execution result.

    Returns:
        The wave's :class:`WaveVerdict`.
    """
    parked = parked_in_result(exec_result)
    if not exec_result.any_failed:
        return WaveVerdict(failed=False, parked_task_ids=parked)
    suffix = f", {len(parked)} awaiting a human" if parked else ""
    return WaveVerdict(
        failed=True,
        parked_task_ids=parked,
        error=f"Wave {wave_idx}: {exec_result.agents_failed} agent(s) failed{suffix}",
    )
