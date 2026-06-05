"""Tests for the rule-based step quality classifier."""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- populate registry side-effect
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.quality.classifier import (
    _DEFAULT_CONFIDENCE_FALLBACK,
    _DEFAULT_CONFIDENCE_RULE_MATCHED,
    RuleBasedStepClassifier,
)
from synthorg.engine.quality.models import StepQuality
from synthorg.engine.stagnation.models import (
    StagnationReason,
    StagnationResult,
    StagnationVerdict,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import get_registry


def _turn(
    *,
    number: int = 1,
    finish: FinishReason = FinishReason.STOP,
    tools: tuple[str, ...] = (),
) -> TurnRecord:
    """Helper to build a minimal TurnRecord."""
    return TurnRecord(
        turn_number=number,
        input_tokens=100,
        output_tokens=50,
        cost=0.01,
        tool_calls_made=tools,
        finish_reason=finish,
    )


@pytest.mark.unit
class TestRuleBasedStepClassifier:
    """RuleBasedStepClassifier classification rules."""

    @pytest.fixture
    def classifier(self) -> RuleBasedStepClassifier:
        return RuleBasedStepClassifier()

    async def test_stagnation_terminate_is_incorrect(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        stagnation = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.8,
        )
        signal = await classifier.classify(
            step_index=0,
            turns=(_turn(number=1, tools=("read",)),),
            termination_reason=TerminationReason.STAGNATION,
            stagnation_result=stagnation,
        )
        assert signal.quality == StepQuality.INCORRECT
        assert signal.confidence == 1.0
        assert "stagnation" in signal.reason.lower()

    async def test_stagnation_inject_prompt_not_incorrect(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        """INJECT_PROMPT is not TERMINATE -- step continues."""
        stagnation = StagnationResult(
            verdict=StagnationVerdict.INJECT_PROMPT,
            corrective_message="Try a different approach",
            repetition_ratio=0.6,
        )
        signal = await classifier.classify(
            step_index=0,
            turns=(
                _turn(number=1, tools=("read",)),
                _turn(number=2, tools=("write",)),
            ),
            termination_reason=TerminationReason.COMPLETED,
            stagnation_result=stagnation,
        )
        assert signal.quality == StepQuality.CORRECT

    async def test_error_termination_is_incorrect(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=1,
            turns=(_turn(number=3),),
            termination_reason=TerminationReason.ERROR,
        )
        assert signal.quality == StepQuality.INCORRECT
        assert signal.confidence == 0.7
        assert "ERROR" in signal.reason

    async def test_final_turn_error_finish_is_incorrect(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(
                _turn(number=1, finish=FinishReason.STOP),
                _turn(number=2, finish=FinishReason.ERROR),
            ),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.quality == StepQuality.INCORRECT
        assert signal.confidence == 0.7

    async def test_completed_with_tools_is_correct(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(
                _turn(number=1, tools=("read",)),
                _turn(number=2, tools=("write",)),
            ),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.quality == StepQuality.CORRECT
        assert signal.confidence == 0.7
        assert "tool calls" in signal.reason.lower()

    async def test_completed_without_tools_is_neutral(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(_turn(number=1),),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.quality == StepQuality.NEUTRAL
        assert signal.confidence == 0.5

    async def test_empty_turns_is_neutral(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.quality == StepQuality.NEUTRAL
        assert signal.confidence == 0.5
        assert "empty" in signal.reason.lower()

    async def test_max_turns_with_tools_is_neutral(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        """MAX_TURNS termination with tool calls is neutral (not completed)."""
        signal = await classifier.classify(
            step_index=0,
            turns=(
                _turn(number=1, tools=("read",)),
                _turn(number=2, tools=("write",)),
            ),
            termination_reason=TerminationReason.MAX_TURNS,
        )
        assert signal.quality == StepQuality.NEUTRAL

    async def test_budget_exhausted_without_tools_is_neutral(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(_turn(number=1),),
            termination_reason=TerminationReason.BUDGET_EXHAUSTED,
        )
        assert signal.quality == StepQuality.NEUTRAL

    async def test_step_index_preserved(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=4,
            turns=(_turn(number=10, tools=("read",)),),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.step_index == 4

    async def test_turn_range_computed(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(
                _turn(number=5),
                _turn(number=6),
                _turn(number=7),
            ),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.turn_range == (5, 7)

    async def test_empty_turns_turn_range_defaults(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        signal = await classifier.classify(
            step_index=0,
            turns=(),
            termination_reason=TerminationReason.COMPLETED,
        )
        assert signal.turn_range == (1, 1)

    async def test_stagnation_takes_priority_over_error(
        self, classifier: RuleBasedStepClassifier
    ) -> None:
        """Stagnation (definitive) should take priority over error termination."""
        stagnation = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            reason=StagnationReason.TOOL_REPETITION,
            repetition_ratio=0.9,
        )
        signal = await classifier.classify(
            step_index=0,
            turns=(_turn(number=1, finish=FinishReason.ERROR),),
            termination_reason=TerminationReason.ERROR,
            stagnation_result=stagnation,
        )
        assert signal.quality == StepQuality.INCORRECT
        assert signal.confidence == 1.0  # Definitive, not 0.7


@pytest.mark.unit
class TestClassifierConfidenceValidation:
    """Constructor validates confidence bounds to ``[0.0, 1.0]``."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"rule_matched_confidence": -0.1},
            {"rule_matched_confidence": 1.5},
            {"fallback_confidence": -0.01},
            {"fallback_confidence": 2.0},
        ],
    )
    def test_out_of_range_confidence_raises(
        self,
        kwargs: dict[str, float],
    ) -> None:
        with pytest.raises(ValueError, match=r"must be in \[0\.0, 1\.0\]"):
            RuleBasedStepClassifier(**kwargs)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"rule_matched_confidence": 0.0},
            {"rule_matched_confidence": 1.0},
            {"fallback_confidence": 0.0},
            {"fallback_confidence": 1.0},
            {"rule_matched_confidence": 0.95, "fallback_confidence": 0.6},
        ],
    )
    def test_in_range_confidence_accepted(
        self,
        kwargs: dict[str, float],
    ) -> None:
        # Must not raise.
        RuleBasedStepClassifier(**kwargs)


@pytest.mark.unit
class TestClassifierDefaultsRegistryDrift:
    """Module defaults must match the settings-registry defaults.

    ``_DEFAULT_CONFIDENCE_RULE_MATCHED`` and
    ``_DEFAULT_CONFIDENCE_FALLBACK`` are fallbacks used when callers
    construct :class:`RuleBasedStepClassifier` without explicit
    ``rule_matched_confidence`` / ``fallback_confidence`` kwargs.  The
    canonical defaults live in the settings registry under the
    ``engine.classifier_rule_matched_confidence`` and
    ``engine.classifier_fallback_confidence`` entries; this test is
    the drift guard that fails if the two sources of truth ever
    diverge.
    """

    @pytest.mark.parametrize(
        ("setting_key", "expected_default"),
        [
            (
                "classifier_rule_matched_confidence",
                _DEFAULT_CONFIDENCE_RULE_MATCHED,
            ),
            (
                "classifier_fallback_confidence",
                _DEFAULT_CONFIDENCE_FALLBACK,
            ),
        ],
    )
    def test_default_matches_registry(
        self,
        setting_key: str,
        expected_default: float,
    ) -> None:
        registry = get_registry()
        registered = registry.get(
            SettingNamespace.ENGINE.value,
            setting_key,
        )
        assert registered is not None
        assert registered.default is not None
        assert float(registered.default) == expected_default
