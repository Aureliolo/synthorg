"""Tests for engine error hierarchy."""

import pytest

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.engine.errors import (
    EngineError,
    ExecutionStateError,
    LoopExecutionError,
    MaxTurnsExceededError,
    NoEligibleAgentError,
    PromptBuildError,
    TaskAssignmentError,
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

    def test_budget_exhausted_error_is_not_engine_error(self) -> None:
        assert not issubclass(BudgetExhaustedError, EngineError)
        err = BudgetExhaustedError("out of budget")
        assert isinstance(err, Exception)
        assert str(err) == "out of budget"

    def test_loop_execution_error_is_engine_error(self) -> None:
        assert issubclass(LoopExecutionError, EngineError)
        err = LoopExecutionError("loop failed")
        assert isinstance(err, EngineError)
        assert str(err) == "loop failed"

    def test_task_assignment_error_is_engine_error(self) -> None:
        assert issubclass(TaskAssignmentError, EngineError)
        err = TaskAssignmentError("assignment failed")
        assert isinstance(err, EngineError)
        assert str(err) == "assignment failed"

    def test_no_eligible_agent_error_is_task_assignment_error(self) -> None:
        assert issubclass(NoEligibleAgentError, TaskAssignmentError)
        assert issubclass(NoEligibleAgentError, EngineError)
        err = NoEligibleAgentError("no agents")
        assert isinstance(err, TaskAssignmentError)
        assert isinstance(err, EngineError)
        assert str(err) == "no agents"
