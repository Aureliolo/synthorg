"""Accuracy-effort ratio computation.

Pure functions for computing the accuracy-effort trade-off metric
from step quality signals, inspired by the MADQA benchmark.
"""

from synthorg.engine.quality.models import (
    AccuracyEffortRatio,
    StepQuality,
    StepQualitySignal,
)
from synthorg.observability import get_logger
from synthorg.observability.events.quality import (
    QUALITY_ACCURACY_EFFORT_COMPUTED,
    QUALITY_WEAK_MODEL_WARNING,
)

logger = get_logger(__name__)

# Weak model trap thresholds.
_WEAK_MODEL_MAX_STEPS: int = 2
_WEAK_MODEL_MIN_ACCURACY: float = 0.8


def compute_accuracy_effort(
    signals: tuple[StepQualitySignal, ...],
    *,
    expected_steps: int = 0,
) -> AccuracyEffortRatio:
    """Compute accuracy-effort ratio from step quality signals.

    Args:
        signals: Quality signals from all steps in the execution.
        expected_steps: Expected number of steps from the plan.
            When 0 or negative, defaults to len(signals).

    Returns:
        Accuracy-effort ratio model.

    Raises:
        ValueError: If signals is empty.
    """
    if not signals:
        msg = "Cannot compute accuracy-effort ratio from empty signals"
        logger.warning(
            QUALITY_ACCURACY_EFFORT_COMPUTED,
            error=msg,
            signal_count=0,
        )
        raise ValueError(msg)

    correct = sum(1 for s in signals if s.quality == StepQuality.CORRECT)
    neutral = sum(1 for s in signals if s.quality == StepQuality.NEUTRAL)
    incorrect = sum(1 for s in signals if s.quality == StepQuality.INCORRECT)
    total = len(signals)

    denominator = max(1, expected_steps) if expected_steps > 0 else total
    effort = total / denominator

    result = AccuracyEffortRatio(
        effort=effort,
        correct_steps=correct,
        neutral_steps=neutral,
        incorrect_steps=incorrect,
        total_steps=total,
    )

    logger.info(
        QUALITY_ACCURACY_EFFORT_COMPUTED,
        accuracy=result.accuracy,
        effort=result.effort,
        ratio=result.ratio,
        correct=correct,
        neutral=neutral,
        incorrect=incorrect,
        total=total,
    )

    # Weak model trap: high accuracy + very few steps may indicate
    # early termination rather than genuine efficiency.
    if total <= _WEAK_MODEL_MAX_STEPS and result.accuracy >= _WEAK_MODEL_MIN_ACCURACY:
        logger.warning(
            QUALITY_WEAK_MODEL_WARNING,
            accuracy=result.accuracy,
            total_steps=total,
            detail=(
                "High accuracy with very few steps -- model may be "
                "terminating early rather than being genuinely efficient"
            ),
        )

    return result
