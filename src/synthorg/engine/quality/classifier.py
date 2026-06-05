"""Step quality classifier protocol and rule-based implementation.

The ``StepQualityClassifier`` protocol allows pluggable classification
strategies (rule-based, LLM-based, hybrid).  The default
``RuleBasedStepClassifier`` uses deterministic heuristics derived from
turn metadata -- no LLM cost.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.quality.models import StepQuality, StepQualitySignal
from synthorg.engine.stagnation.models import (
    StagnationResult,
    StagnationVerdict,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.quality import (
    QUALITY_CLASSIFIER_CONFIG_INVALID,
    QUALITY_STEP_CLASSIFIED,
)
from synthorg.providers.enums import FinishReason

logger = get_logger(__name__)

# Confidence levels for rule-based classification.
# ``_CONFIDENCE_DEFINITIVE`` is not operator-tunable (a definitive
# stagnation verdict is a boolean -- no ambiguity to model).  The
# rule-matched and fallback confidences default to the values
# registered under the ``engine.classifier_rule_matched_confidence``
# and ``engine.classifier_fallback_confidence`` settings and can be
# overridden per-instance via the ``RuleBasedStepClassifier``
# constructor.  Both registry entries are ``restart_required=True``
# -- operators edit them in the settings store/dashboard and the
# service must be restarted for the new values to flow through
# startup wiring into newly constructed classifiers (no live
# hot-apply path exists).
_CONFIDENCE_DEFINITIVE: float = 1.0
_DEFAULT_CONFIDENCE_RULE_MATCHED: float = 0.7
_DEFAULT_CONFIDENCE_FALLBACK: float = 0.5


# RuleBasedStepClassifier impl in same file; pluggable design
# (rule-based / LLM-based / hybrid).
@runtime_checkable
class StepQualityClassifier(Protocol):
    """Protocol for step-level quality classification.

    Async to allow future LLM-based implementations without
    breaking the interface.
    """

    async def classify(
        self,
        *,
        step_index: int,
        turns: tuple[TurnRecord, ...],
        termination_reason: TerminationReason,
        stagnation_result: StagnationResult | None = None,
    ) -> StepQualitySignal:
        """Classify a completed step's quality.

        Args:
            step_index: Zero-based index of the step in the plan.
            turns: Turn records from this step's execution.
            termination_reason: Why the step's execution stopped.
            stagnation_result: Stagnation check result, if any.

        Returns:
            Quality signal with ternary classification and confidence.
        """
        ...


class RuleBasedStepClassifier:
    """Deterministic step quality classifier using turn metadata.

    Classification rules (evaluated in order):

    1. **INCORRECT** (definitive): Step terminated by stagnation
       (TERMINATE verdict).
    2. **INCORRECT** (rule-matched): Termination reason is ERROR,
       or final turn finished with an error reason.
    3. **CORRECT** (rule-matched): Step completed normally with at
       least one tool call producing results.
    4. **NEUTRAL** (fallback): Everything else -- exploratory steps,
       partial progress, no tool calls.

    Args:
        rule_matched_confidence: Confidence score attached to
            rule-matched verdicts.  Bridged from the
            ``engine.classifier_rule_matched_confidence`` setting
            (``restart_required``); callers that resolve the setting
            at startup should pass the resolved float so the newly
            constructed classifier carries the current value.  Must
            be in ``[0.0, 1.0]``.
        fallback_confidence: Confidence score attached to the
            NEUTRAL fallback verdict.  Bridged from
            ``engine.classifier_fallback_confidence``
            (``restart_required``).  Must be in ``[0.0, 1.0]``.

    Raises:
        ValueError: If either confidence is outside ``[0.0, 1.0]``.
    """

    __slots__ = ("_fallback_confidence", "_rule_matched_confidence")

    def __init__(
        self,
        *,
        rule_matched_confidence: float = _DEFAULT_CONFIDENCE_RULE_MATCHED,
        fallback_confidence: float = _DEFAULT_CONFIDENCE_FALLBACK,
    ) -> None:
        if not 0.0 <= rule_matched_confidence <= 1.0:
            msg = (
                "rule_matched_confidence must be in [0.0, 1.0];"
                f" got {rule_matched_confidence!r}"
            )
            logger.warning(
                QUALITY_CLASSIFIER_CONFIG_INVALID,
                error=msg,
                parameter="rule_matched_confidence",
                value=rule_matched_confidence,
            )
            raise ValueError(msg)
        if not 0.0 <= fallback_confidence <= 1.0:
            msg = (
                "fallback_confidence must be in [0.0, 1.0];"
                f" got {fallback_confidence!r}"
            )
            logger.warning(
                QUALITY_CLASSIFIER_CONFIG_INVALID,
                error=msg,
                parameter="fallback_confidence",
                value=fallback_confidence,
            )
            raise ValueError(msg)
        self._rule_matched_confidence = rule_matched_confidence
        self._fallback_confidence = fallback_confidence

    async def classify(
        self,
        *,
        step_index: int,
        turns: tuple[TurnRecord, ...],
        termination_reason: TerminationReason,
        stagnation_result: StagnationResult | None = None,
    ) -> StepQualitySignal:
        """Classify a step using deterministic heuristics.

        Returns:
            A :class:`StepQualitySignal` carrying quality, confidence,
            and a short reason text describing which rule fired.
        """
        turn_range = _compute_turn_range(turns)

        # Rule 1: stagnation-triggered termination is definitively incorrect.
        if (
            stagnation_result is not None
            and stagnation_result.verdict == StagnationVerdict.TERMINATE
        ):
            signal = StepQualitySignal(
                quality=StepQuality.INCORRECT,
                confidence=_CONFIDENCE_DEFINITIVE,
                reason="Step terminated by stagnation detection",
                step_index=step_index,
                turn_range=turn_range,
            )
            _log_classification(signal)
            return signal

        # Rule 2: error termination or error finish reason.
        if termination_reason == TerminationReason.ERROR:
            signal = StepQualitySignal(
                quality=StepQuality.INCORRECT,
                confidence=self._rule_matched_confidence,
                reason="Step terminated with ERROR",
                step_index=step_index,
                turn_range=turn_range,
            )
            _log_classification(signal)
            return signal

        if turns and turns[-1].finish_reason == FinishReason.ERROR:
            signal = StepQualitySignal(
                quality=StepQuality.INCORRECT,
                confidence=self._rule_matched_confidence,
                reason="Final turn finished with error",
                step_index=step_index,
                turn_range=turn_range,
            )
            _log_classification(signal)
            return signal

        # Rule 3: normal completion with tool calls -> correct.
        has_tool_calls = any(len(t.tool_calls_made) > 0 for t in turns)
        if termination_reason == TerminationReason.COMPLETED and has_tool_calls:
            signal = StepQualitySignal(
                quality=StepQuality.CORRECT,
                confidence=self._rule_matched_confidence,
                reason="Step completed with tool calls",
                step_index=step_index,
                turn_range=turn_range,
            )
            _log_classification(signal)
            return signal

        # Rule 4: fallback -- neutral/exploratory.
        reason = "Exploratory step (no definitive outcome signal)"
        if not turns:
            reason = "Empty step (no turns executed)"
        elif not has_tool_calls:
            reason = "Step completed without tool calls"

        signal = StepQualitySignal(
            quality=StepQuality.NEUTRAL,
            confidence=self._fallback_confidence,
            reason=reason,
            step_index=step_index,
            turn_range=turn_range,
        )
        _log_classification(signal)
        return signal


def _compute_turn_range(turns: tuple[TurnRecord, ...]) -> tuple[int, int]:
    """Extract inclusive (start, end) turn numbers from turn records.

    Returns:
        ``(first_turn_number, last_turn_number)``; ``(1, 1)`` when
        ``turns`` is empty.
    """
    if not turns:
        return (1, 1)
    return (turns[0].turn_number, turns[-1].turn_number)


def _log_classification(signal: StepQualitySignal) -> None:
    """Log a quality classification event."""
    logger.debug(
        QUALITY_STEP_CLASSIFIED,
        step_index=signal.step_index,
        quality=signal.quality.value,
        confidence=signal.confidence,
        reason=signal.reason,
        turn_range=signal.turn_range,
    )
