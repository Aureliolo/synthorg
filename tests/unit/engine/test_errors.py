"""Tests for engine error hierarchy."""

import pytest

from ai_company.engine.errors import (
    BudgetExhaustedError,
    EngineError,
    ExecutionStateError,
    LoopExecutionError,
    MaxTurnsExceededError,
    PromptBuildError,
)


@pytest.mark.unit
class TestEngineErrorHierarchy:
    """Engine error hierarchy and inheritance."""

    def test_execution_state_error_is_engine_error(self) -> None:
        assert issubclass(ExecutionStateError, EngineError)
        err = ExecutionStateError("test")
        assert isinstance(err, EngineError)

    def test_max_turns_exceeded_error_is_engine_error(self) -> None:
        assert issubclass(MaxTurnsExceededError, EngineError)
        err = MaxTurnsExceededError("exceeded")
        assert isinstance(err, EngineError)
        assert str(err) == "exceeded"

    def test_prompt_build_error_is_engine_error(self) -> None:
        assert issubclass(PromptBuildError, EngineError)
        err = PromptBuildError("test")
        assert isinstance(err, EngineError)

    def test_budget_exhausted_error_is_engine_error(self) -> None:
        assert issubclass(BudgetExhaustedError, EngineError)
        err = BudgetExhaustedError("out of budget")
        assert isinstance(err, EngineError)
        assert str(err) == "out of budget"

    def test_loop_execution_error_is_engine_error(self) -> None:
        assert issubclass(LoopExecutionError, EngineError)
        err = LoopExecutionError("loop failed")
        assert isinstance(err, EngineError)
        assert str(err) == "loop failed"
