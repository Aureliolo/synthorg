"""Tests for execution loop protocol and supporting models."""

import pytest
from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionLoop,
    ExecutionResult,
    TerminationReason,
    make_budget_checker,
)
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.react_loop import ReactLoop
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, TokenUsage


@pytest.mark.unit
class TestTerminationReason:
    """TerminationReason enum values."""

    def test_values(self) -> None:
        assert TerminationReason.COMPLETED.value == "completed"
        assert TerminationReason.MAX_TURNS.value == "max_turns"
        assert TerminationReason.BUDGET_EXHAUSTED.value == "budget_exhausted"
        assert TerminationReason.SHUTDOWN.value == "shutdown"
        assert TerminationReason.ERROR.value == "error"
        assert TerminationReason.PARKED.value == "parked"
        assert TerminationReason.STAGNATION.value == "stagnation"
        assert TerminationReason.CANCELLED.value == "cancelled"

    def test_member_count(self) -> None:
        assert len(TerminationReason) == 8


@pytest.mark.unit
class TestTurnRecord:
    """TurnRecord frozen model."""

    def test_creation(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            tool_calls_made=("search",),
            finish_reason=FinishReason.TOOL_USE,
        )
        assert record.turn_number == 1
        assert record.input_tokens == 100
        assert record.output_tokens == 50
        assert record.cost == 0.01
        assert record.tool_calls_made == ("search",)
        assert record.finish_reason == FinishReason.TOOL_USE

    def test_frozen(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            finish_reason=FinishReason.STOP,
        )
        with pytest.raises(ValidationError):
            record.turn_number = 2  # type: ignore[misc]

    def test_defaults(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=0,
            output_tokens=0,
            cost=0.0,
            finish_reason=FinishReason.STOP,
        )
        assert record.tool_calls_made == ()
        assert record.tool_call_fingerprints == ()

    def test_tool_call_fingerprints(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            tool_calls_made=("search",),
            tool_call_fingerprints=("search:abc1234567890123",),
            finish_reason=FinishReason.TOOL_USE,
        )
        assert record.tool_call_fingerprints == ("search:abc1234567890123",)

    def test_total_tokens_computed(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            finish_reason=FinishReason.STOP,
        )
        assert record.total_tokens == 150

    def test_total_tokens_zero(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=0,
            output_tokens=0,
            cost=0.0,
            finish_reason=FinishReason.STOP,
        )
        assert record.total_tokens == 0

    def test_call_category_none_default(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
        )
        assert record.call_category is None

    def test_call_category_productive(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        assert record.call_category == LLMCallCategory.PRODUCTIVE

    def test_call_category_coordination(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            call_category=LLMCallCategory.COORDINATION,
        )
        assert record.call_category == LLMCallCategory.COORDINATION

    def test_call_category_system(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            call_category=LLMCallCategory.SYSTEM,
        )
        assert record.call_category == LLMCallCategory.SYSTEM

    def test_turn_number_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnRecord(
                turn_number=0,
                input_tokens=10,
                output_tokens=5,
                cost=0.01,
                finish_reason=FinishReason.STOP,
            )

    def test_negative_input_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnRecord(
                turn_number=1,
                input_tokens=-1,
                output_tokens=5,
                cost=0.01,
                finish_reason=FinishReason.STOP,
            )

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=-0.01,
                finish_reason=FinishReason.STOP,
            )


@pytest.mark.unit
class TestTurnRecordAnalyticsFields:
    """New per-call analytics fields on TurnRecord."""

    def _base(self, *, finish_reason: FinishReason = FinishReason.STOP) -> TurnRecord:
        return TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=finish_reason,
        )

    def test_latency_ms_default_none(self) -> None:
        assert self._base().latency_ms is None

    def test_latency_ms_positive_accepted(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            latency_ms=250.0,
        )
        assert record.latency_ms == 250.0

    def test_latency_ms_zero_accepted(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            latency_ms=0.0,
        )
        assert record.latency_ms == 0.0

    def test_latency_ms_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                finish_reason=FinishReason.STOP,
                latency_ms=-1.0,
            )

    def test_cache_hit_default_none(self) -> None:
        assert self._base().cache_hit is None

    def test_cache_hit_true(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            cache_hit=True,
        )
        assert record.cache_hit is True

    def test_cache_hit_false(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            cache_hit=False,
        )
        assert record.cache_hit is False

    def test_retry_count_default_none(self) -> None:
        assert self._base().retry_count is None

    def test_retry_count_positive_accepted(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            retry_count=3,
        )
        assert record.retry_count == 3

    def test_retry_count_zero_accepted(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            retry_count=0,
        )
        assert record.retry_count == 0

    def test_retry_count_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                finish_reason=FinishReason.STOP,
                retry_count=-1,
            )

    def test_retry_reason_default_none(self) -> None:
        assert self._base().retry_reason is None

    def test_retry_reason_set(self) -> None:
        record = TurnRecord(
            turn_number=1,
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
            finish_reason=FinishReason.STOP,
            retry_count=1,
            retry_reason="RateLimitError",
        )
        assert record.retry_reason == "RateLimitError"

    def test_success_true_for_stop(self) -> None:
        assert self._base(finish_reason=FinishReason.STOP).success is True

    def test_success_true_for_tool_use(self) -> None:
        assert self._base(finish_reason=FinishReason.TOOL_USE).success is True

    def test_success_true_for_max_tokens(self) -> None:
        assert self._base(finish_reason=FinishReason.MAX_TOKENS).success is True

    def test_success_false_for_error(self) -> None:
        assert self._base(finish_reason=FinishReason.ERROR).success is False

    def test_success_false_for_content_filter(self) -> None:
        assert self._base(finish_reason=FinishReason.CONTENT_FILTER).success is False


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
        )
        with pytest.raises(ValidationError):
            result.termination_reason = TerminationReason.ERROR  # type: ignore[misc]

    def test_total_tool_calls_computed(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        turns = (
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                tool_calls_made=("search", "read"),
                finish_reason=FinishReason.TOOL_USE,
            ),
            TurnRecord(
                turn_number=2,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                tool_calls_made=("write",),
                finish_reason=FinishReason.STOP,
            ),
        )
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        assert result.total_tool_calls == 3

    def test_error_message_required_when_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        with pytest.raises(
            ValidationError,
            match="error_message is required",
        ):
            ExecutionResult(
                context=sample_agent_context,
                termination_reason=TerminationReason.ERROR,
                turns=(),
            )

    def test_error_message_forbidden_when_not_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        with pytest.raises(
            ValidationError,
            match="error_message must be None",
        ):
            ExecutionResult(
                context=sample_agent_context,
                termination_reason=TerminationReason.COMPLETED,
                turns=(),
                error_message="unexpected",
            )


@pytest.mark.unit
class TestProtocolConformance:
    """ReactLoop and PlanExecuteLoop satisfy ExecutionLoop protocol."""

    def test_react_loop_is_execution_loop(self) -> None:
        loop = ReactLoop()
        assert isinstance(loop, ExecutionLoop)

    def test_react_loop_type(self) -> None:
        loop = ReactLoop()
        assert loop.get_loop_type() == "react"

    def test_plan_execute_loop_is_execution_loop(self) -> None:
        loop = PlanExecuteLoop()
        assert isinstance(loop, ExecutionLoop)

    def test_plan_execute_loop_type(self) -> None:
        loop = PlanExecuteLoop()
        assert loop.get_loop_type() == "plan_execute"


@pytest.mark.unit
class TestMakeBudgetChecker:
    """Tests for make_budget_checker factory function."""

    @staticmethod
    def _make_task(budget_limit: float) -> Task:
        return Task(
            id="task-budget-001",
            title="Test task",
            description="A task for budget checker testing.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="tester",
            assigned_to="test-agent",
            estimated_complexity=Complexity.SIMPLE,
            budget_limit=budget_limit,
            status=TaskStatus.ASSIGNED,
        )

    def test_zero_budget_returns_none(self) -> None:
        task = self._make_task(0.0)
        assert make_budget_checker(task) is None

    def test_positive_budget_returns_callable(self) -> None:
        task = self._make_task(5.0)
        checker = make_budget_checker(task)
        assert checker is not None
        assert callable(checker)

    def test_checker_returns_false_under_limit(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        task = self._make_task(10.0)
        checker = make_budget_checker(task)
        assert checker is not None
        # Default context has zero cost
        assert checker(sample_agent_context) is False

    def test_checker_returns_true_at_limit(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        task = self._make_task(0.01)
        checker = make_budget_checker(task)
        assert checker is not None
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
        )
        msg = ChatMessage(role=MessageRole.ASSISTANT, content="done")
        ctx = sample_agent_context.with_turn_completed(usage, msg)
        assert checker(ctx) is True

    def test_checker_returns_true_over_limit(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        task = self._make_task(0.005)
        checker = make_budget_checker(task)
        assert checker is not None
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
        )
        msg = ChatMessage(role=MessageRole.ASSISTANT, content="done")
        ctx = sample_agent_context.with_turn_completed(usage, msg)
        assert checker(ctx) is True
