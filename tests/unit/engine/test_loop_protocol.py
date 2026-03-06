"""Tests for execution loop protocol and supporting models."""

import pytest
from pydantic import ValidationError

from ai_company.engine.context import AgentContext  # noqa: TC001
from ai_company.engine.loop_protocol import (
    ExecutionLoop,
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from ai_company.engine.react_loop import ReactLoop
from ai_company.providers.enums import FinishReason


@pytest.mark.unit
class TestTerminationReason:
    """TerminationReason enum values."""

    def test_values(self) -> None:
        assert TerminationReason.COMPLETED.value == "completed"
        assert TerminationReason.MAX_TURNS.value == "max_turns"
        assert TerminationReason.BUDGET_EXHAUSTED.value == "budget_exhausted"
        assert TerminationReason.ERROR.value == "error"

    def test_member_count(self) -> None:
        assert len(TerminationReason) == 4


@pytest.mark.unit
class TestTurnRecord:
    """TurnRecord frozen model."""

    def test_creation(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            tool_calls_made=("search",),
            finish_reason=FinishReason.TOOL_USE,
        )
        assert record.turn_number == 1
        assert record.input_tokens == 100
        assert record.output_tokens == 50
        assert record.cost_usd == 0.01
        assert record.tool_calls_made == ("search",)
        assert record.finish_reason == FinishReason.TOOL_USE

    def test_frozen(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            finish_reason=FinishReason.STOP,
        )
        with pytest.raises(ValidationError):
            record.turn_number = 2  # type: ignore[misc]

    def test_defaults(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            finish_reason=FinishReason.STOP,
        )
        assert record.tool_calls_made == ()


@pytest.mark.unit
class TestExecutionResult:
    """ExecutionResult frozen model."""

    def test_creation(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
            turns=(),
            total_tool_calls=0,
        )
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 0
        assert result.error_message is None
        assert result.metadata == {}

    def test_with_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.ERROR,
            turns=(),
            total_tool_calls=0,
            error_message="something went wrong",
        )
        assert result.error_message == "something went wrong"

    def test_with_metadata(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
            turns=(),
            total_tool_calls=0,
            metadata={"plan": "step1"},
        )
        assert result.metadata == {"plan": "step1"}

    def test_frozen(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
            turns=(),
            total_tool_calls=0,
        )
        with pytest.raises(ValidationError):
            result.total_tool_calls = 5  # type: ignore[misc]


@pytest.mark.unit
class TestProtocolConformance:
    """ReactLoop satisfies ExecutionLoop protocol."""

    def test_react_loop_is_execution_loop(self) -> None:
        loop = ReactLoop()
        assert isinstance(loop, ExecutionLoop)

    def test_react_loop_type(self) -> None:
        loop = ReactLoop()
        assert loop.get_loop_type() == "react"
