"""Tests for stagnation detection models."""

import pytest
from pydantic import ValidationError

from synthorg.engine.stagnation.models import (
    StagnationConfig,
    StagnationReason,
    StagnationResult,
    StagnationVerdict,
)


@pytest.mark.unit
class TestStagnationVerdict:
    """StagnationVerdict enum values."""

    def test_values(self) -> None:
        assert StagnationVerdict.NO_STAGNATION.value == "no_stagnation"
        assert StagnationVerdict.INJECT_PROMPT.value == "inject_prompt"
        assert StagnationVerdict.TERMINATE.value == "terminate"

    def test_member_count(self) -> None:
        assert len(StagnationVerdict) == 3


@pytest.mark.unit
class TestStagnationConfig:
    """StagnationConfig frozen model validation."""

    def test_defaults(self) -> None:
        config = StagnationConfig()
        assert config.window_size == 5
        assert config.repetition_threshold == 0.6
        assert config.cycle_detection is True
        assert config.max_corrections == 1
        assert config.min_tool_turns == 2

    def test_frozen(self) -> None:
        config = StagnationConfig()
        with pytest.raises(ValidationError):
            config.window_size = 9  # type: ignore[misc]

    def test_custom_values(self) -> None:
        config = StagnationConfig(
            window_size=10,
            repetition_threshold=0.8,
            cycle_detection=False,
            max_corrections=3,
            min_tool_turns=5,
        )
        assert config.window_size == 10
        assert config.repetition_threshold == 0.8
        assert config.cycle_detection is False
        assert config.max_corrections == 3
        assert config.min_tool_turns == 5

    def test_window_size_lower_bound(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 2"):
            StagnationConfig(window_size=1)

    def test_window_size_upper_bound(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal to 50"):
            StagnationConfig(window_size=51)

    def test_repetition_threshold_lower_bound(self) -> None:
        config = StagnationConfig(repetition_threshold=0.0)
        assert config.repetition_threshold == 0.0

    def test_repetition_threshold_upper_bound(self) -> None:
        config = StagnationConfig(repetition_threshold=1.0)
        assert config.repetition_threshold == 1.0

    def test_repetition_threshold_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StagnationConfig(repetition_threshold=-0.1)

    def test_repetition_threshold_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StagnationConfig(repetition_threshold=1.1)

    def test_max_corrections_zero(self) -> None:
        config = StagnationConfig(max_corrections=0)
        assert config.max_corrections == 0

    def test_max_corrections_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StagnationConfig(max_corrections=-1)

    def test_min_tool_turns_lower_bound(self) -> None:
        config = StagnationConfig(min_tool_turns=1)
        assert config.min_tool_turns == 1

    def test_min_tool_turns_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StagnationConfig(min_tool_turns=0)

    def test_min_tool_turns_exceeds_window_size_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"min_tool_turns.*exceeds.*window_size",
        ):
            StagnationConfig(window_size=3, min_tool_turns=4)


@pytest.mark.unit
class TestStagnationResult:
    """StagnationResult frozen model validation."""

    def test_no_stagnation(self) -> None:
        result = StagnationResult(
            verdict=StagnationVerdict.NO_STAGNATION,
        )
        assert result.verdict == StagnationVerdict.NO_STAGNATION
        assert result.corrective_message is None
        assert result.repetition_ratio == 0.0
        assert result.cycle_length is None
        assert result.details == {}

    def test_inject_prompt_requires_message(self) -> None:
        with pytest.raises(
            ValidationError,
            match="corrective_message is required",
        ):
            StagnationResult(
                verdict=StagnationVerdict.INJECT_PROMPT,
            )

    def test_inject_prompt_with_message(self) -> None:
        result = StagnationResult(
            verdict=StagnationVerdict.INJECT_PROMPT,
            reason=StagnationReason.TOOL_REPETITION,
            corrective_message="Try different tools.",
            repetition_ratio=0.7,
        )
        assert result.corrective_message == "Try different tools."

    def test_terminate_forbids_message(self) -> None:
        with pytest.raises(
            ValidationError,
            match="corrective_message must be None",
        ):
            StagnationResult(
                verdict=StagnationVerdict.TERMINATE,
                reason=StagnationReason.TOOL_REPETITION,
                corrective_message="should not be here",
            )

    def test_no_stagnation_forbids_message(self) -> None:
        with pytest.raises(
            ValidationError,
            match="corrective_message must be None",
        ):
            StagnationResult(
                verdict=StagnationVerdict.NO_STAGNATION,
                corrective_message="should not be here",
            )

    def test_terminate_without_message(self) -> None:
        result = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            reason=StagnationReason.CYCLE_DETECTION,
            repetition_ratio=0.9,
            cycle_length=2,
        )
        assert result.verdict == StagnationVerdict.TERMINATE
        assert result.corrective_message is None
        assert result.cycle_length == 2

    def test_frozen(self) -> None:
        result = StagnationResult(
            verdict=StagnationVerdict.NO_STAGNATION,
        )
        with pytest.raises(ValidationError):
            result.verdict = StagnationVerdict.TERMINATE  # type: ignore[misc]

    def test_repetition_ratio_bounds(self) -> None:
        StagnationResult(
            verdict=StagnationVerdict.NO_STAGNATION,
            repetition_ratio=0.0,
        )
        StagnationResult(
            verdict=StagnationVerdict.NO_STAGNATION,
            repetition_ratio=1.0,
        )
        with pytest.raises(ValidationError):
            StagnationResult(
                verdict=StagnationVerdict.NO_STAGNATION,
                repetition_ratio=-0.1,
            )
        with pytest.raises(ValidationError):
            StagnationResult(
                verdict=StagnationVerdict.NO_STAGNATION,
                repetition_ratio=1.1,
            )

    def test_details_forward_compatible(self) -> None:
        result = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            reason=StagnationReason.TOOL_REPETITION,
            repetition_ratio=0.8,
            details={"repeated_tools": ["search:abc123"]},
        )
        assert result.details == {"repeated_tools": ["search:abc123"]}
