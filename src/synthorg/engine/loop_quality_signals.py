"""Whole-run quality signals for a loop with no per-step boundary.

ReAct is turn-based, so there is no step to classify as it closes; the run
itself is the step. Every visible exit routes its result through here, which
is why this lives beside the loop rather than inside it: the rule is about
what a signal-less result means to the health pipeline, not about the loop's
control flow.
"""

from synthorg.engine.loop_helpers import classify_step
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.quality.models import StepQualitySignal
from synthorg.execution.turn import TurnRecord


async def whole_run_signals(
    classifier: StepQualityClassifier | None,
    turns: list[TurnRecord],
    termination_reason: TerminationReason,
) -> tuple[StepQualitySignal, ...]:
    """Classify the whole run as a single step signal.

    Args:
        classifier: The step-quality classifier, or ``None`` when unwired.
        turns: Every turn the run recorded.
        termination_reason: How the run ended.

    Returns:
        A one-tuple with the run's :class:`StepQualitySignal`, or an
        empty tuple when no classifier is wired.
    """
    signal = await classify_step(
        classifier,
        step_index=0,
        step_turns=tuple(turns),
        termination_reason=termination_reason,
    )
    return (signal,) if signal is not None else ()


async def attach_whole_run_signals(
    result: ExecutionResult,
    turns: list[TurnRecord],
    classifier: StepQualityClassifier | None,
) -> ExecutionResult:
    """Attach the whole-run quality signal to a terminating result.

    Every visible loop exit (shutdown / budget / cancel / provider-error /
    stagnation / tool outcome / completion) routes its result through here so
    the health pipeline never receives an empty ``quality_signals`` for a run
    that actually produced turns.

    Args:
        result: The terminating result.
        turns: Every turn the run recorded.
        classifier: The step-quality classifier, or ``None`` when unwired.

    Returns:
        The result with ``quality_signals`` populated, or unchanged
        when the run produced no turns to classify.
    """
    if not turns:
        return result
    return result.model_copy(
        update={
            "quality_signals": await whole_run_signals(
                classifier,
                turns,
                result.termination_reason,
            )
        }
    )
