"""Unit tests for AgentRunResult model and format_task_instruction helper."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import Priority, SeniorityLevel, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
    make_budget_checker,
)
from synthorg.engine.prompt import SystemPrompt, format_task_instruction
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, TokenUsage, ToolCall


def _test_identity() -> AgentIdentity:
    """Create a minimal AgentIdentity for standalone result tests."""
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
    )


def _make_run_result(  # noqa: PLR0913
    *,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
    turns: tuple[TurnRecord, ...] = (),
    cost_usd: float = 0.05,
    error_message: str | None = None,
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    duration_seconds: float = 1.5,
) -> AgentRunResult:
    """Build an AgentRunResult directly for focused unit tests."""
    identity = _test_identity()
    ctx = AgentContext.from_identity(identity)
    # Apply the desired accumulated cost
    ctx = ctx.model_copy(
        update={
            "accumulated_cost": TokenUsage(
                input_tokens=100,
                output_tokens=50,
                cost_usd=cost_usd,
            ),
        },
    )
    execution = ExecutionResult(
        context=ctx,
        termination_reason=termination_reason,
        turns=turns,
        error_message=error_message,
    )
    prompt = SystemPrompt(
        content="Test prompt",
        template_version="1.0",
        estimated_tokens=10,
        sections=("identity",),
        metadata={"agent_id": agent_id},
    )
    return AgentRunResult(
        execution_result=execution,
        system_prompt=prompt,
        duration_seconds=duration_seconds,
        agent_id=agent_id,
        task_id=task_id,
    )


@pytest.mark.unit
class TestAgentRunResultFrozen:
    """AgentRunResult is frozen — field reassignment raises."""

    def test_frozen_execution_result(self) -> None:
        result = _make_run_result()
        with pytest.raises(ValidationError):
            result.execution_result = None  # type: ignore[assignment,misc]

    def test_frozen_duration(self) -> None:
        result = _make_run_result()
        with pytest.raises(ValidationError):
            result.duration_seconds = 999.0  # type: ignore[misc]

    def test_frozen_agent_id(self) -> None:
        result = _make_run_result()
        with pytest.raises(ValidationError):
            result.agent_id = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestAgentRunResultComputedFields:
    """Computed fields delegate correctly to execution_result."""

    def test_termination_reason_completed(self) -> None:
        result = _make_run_result(termination_reason=TerminationReason.COMPLETED)
        assert result.termination_reason == TerminationReason.COMPLETED

    def test_termination_reason_error(self) -> None:
        result = _make_run_result(
            termination_reason=TerminationReason.ERROR,
            error_message="something failed",
        )
        assert result.termination_reason == TerminationReason.ERROR

    def test_total_turns_zero(self) -> None:
        result = _make_run_result(turns=())
        assert result.total_turns == 0

    def test_total_turns_multiple(self) -> None:
        turns = tuple(
            TurnRecord(
                turn_number=i,
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
                finish_reason=FinishReason.STOP,
            )
            for i in range(1, 4)
        )
        result = _make_run_result(turns=turns)
        assert result.total_turns == 3

    def test_total_cost_usd(self) -> None:
        result = _make_run_result(cost_usd=0.123)
        assert result.total_cost_usd == pytest.approx(0.123)

    def test_is_success_true(self) -> None:
        result = _make_run_result(termination_reason=TerminationReason.COMPLETED)
        assert result.is_success is True

    def test_is_success_false_on_error(self) -> None:
        result = _make_run_result(
            termination_reason=TerminationReason.ERROR,
            error_message="err",
        )
        assert result.is_success is False

    def test_is_success_false_on_max_turns(self) -> None:
        result = _make_run_result(termination_reason=TerminationReason.MAX_TURNS)
        assert result.is_success is False

    def test_is_success_false_on_budget(self) -> None:
        result = _make_run_result(
            termination_reason=TerminationReason.BUDGET_EXHAUSTED,
        )
        assert result.is_success is False

    def test_is_success_false_on_shutdown(self) -> None:
        result = _make_run_result(
            termination_reason=TerminationReason.SHUTDOWN,
        )
        assert result.is_success is False


@pytest.mark.unit
class TestAgentRunResultValidation:
    """Field validation on AgentRunResult."""

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_run_result(duration_seconds=-1.0)

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_run_result(agent_id="   ")

    def test_task_id_none_allowed(self) -> None:
        """task_id=None is valid for future taskless runs."""
        identity = _test_identity()
        ctx = AgentContext.from_identity(identity)
        execution = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
        )
        prompt = SystemPrompt(
            content="",
            template_version="1.0",
            estimated_tokens=0,
            sections=(),
            metadata={},
        )
        result = AgentRunResult(
            execution_result=execution,
            system_prompt=prompt,
            duration_seconds=0.0,
            agent_id="agent-001",
            task_id=None,
        )
        assert result.task_id is None


@pytest.mark.unit
class TestFormatTaskInstruction:
    """Test format_task_instruction helper."""

    def test_basic_format(self, sample_task_with_criteria: Task) -> None:
        result = format_task_instruction(sample_task_with_criteria)

        assert "# Task: Implement authentication module" in result
        assert "JWT-based authentication" in result
        assert "## Acceptance Criteria" in result
        assert "- Login endpoint returns JWT token" in result
        assert "$5.00 USD" in result

    def test_deadline_included(self, sample_task_with_criteria: Task) -> None:
        result = format_task_instruction(sample_task_with_criteria)
        assert "**Deadline:** 2026-04-01T00:00:00" in result

    def test_deadline_has_blank_line_separator(
        self,
        sample_task_with_criteria: Task,
    ) -> None:
        """Deadline block has a blank line before it (consistent with budget)."""
        result = format_task_instruction(sample_task_with_criteria)
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("**Deadline:**"):
                assert lines[i - 1] == "", "Expected blank line before deadline"
                break
        else:
            pytest.fail("Deadline line not found in output")

    def test_no_criteria_no_budget(self) -> None:
        task = Task(
            id="task-simple",
            title="Simple task",
            description="Do the thing.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
        )
        result = format_task_instruction(task)

        assert "# Task: Simple task" in result
        assert "Do the thing." in result
        assert "Acceptance Criteria" not in result
        assert "Budget" not in result

    def test_deadline_only_no_budget(self) -> None:
        """Deadline-only task still gets blank-line separator."""
        task = Task(
            id="task-deadline",
            title="Deadline task",
            description="Has deadline only.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=0.0,
            deadline="2026-06-01T00:00:00+00:00",
        )
        result = format_task_instruction(task)

        assert "**Deadline:**" in result
        assert "Budget" not in result
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("**Deadline:**"):
                assert lines[i - 1] == "", "Expected blank line before deadline"
                break

    def test_budget_only_no_deadline(self) -> None:
        task = Task(
            id="task-budget",
            title="Budget task",
            description="Has budget only.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=10.0,
        )
        result = format_task_instruction(task)

        assert "$10.00 USD" in result
        assert "Deadline" not in result


@pytest.mark.unit
class TestMakeBudgetChecker:
    """Test make_budget_checker closure logic."""

    def test_returns_none_for_zero_budget(self) -> None:
        task = Task(
            id="task-free",
            title="Free",
            description="No budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=0.0,
        )
        assert make_budget_checker(task) is None

    def test_returns_none_for_default_budget(self) -> None:
        """Default budget_limit (0.0) returns None."""
        task = Task(
            id="task-default",
            title="Default",
            description="Default budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
        )
        assert make_budget_checker(task) is None

    def test_returns_callable_for_positive_budget(self) -> None:
        task = Task(
            id="task-b",
            title="Budgeted",
            description="Has budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=5.0,
        )
        checker = make_budget_checker(task)
        assert checker is not None
        assert callable(checker)

    def test_checker_returns_false_under_budget(self) -> None:
        task = Task(
            id="task-b",
            title="Budgeted",
            description="Has budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=5.0,
        )
        checker = make_budget_checker(task)
        assert checker is not None

        identity = _test_identity()
        ctx = AgentContext.from_identity(identity)
        ctx = ctx.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=4.99,
                ),
            },
        )
        assert checker(ctx) is False

    def test_checker_returns_true_at_exact_budget(self) -> None:
        """Boundary: cost_usd == limit returns True (>= comparison)."""
        task = Task(
            id="task-b",
            title="Budgeted",
            description="Has budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=5.0,
        )
        checker = make_budget_checker(task)
        assert checker is not None

        identity = _test_identity()
        ctx = AgentContext.from_identity(identity)
        ctx = ctx.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=5.0,
                ),
            },
        )
        assert checker(ctx) is True

    def test_checker_returns_true_over_budget(self) -> None:
        task = Task(
            id="task-b",
            title="Budgeted",
            description="Has budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
            budget_limit=5.0,
        )
        checker = make_budget_checker(task)
        assert checker is not None

        identity = _test_identity()
        ctx = AgentContext.from_identity(identity)
        ctx = ctx.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=5.01,
                ),
            },
        )
        assert checker(ctx) is True


def _make_result_with_messages(
    *messages: ChatMessage,
) -> AgentRunResult:
    """Build an AgentRunResult with specific messages in conversation."""
    identity = _test_identity()
    ctx = AgentContext.from_identity(identity)
    for msg in messages:
        ctx = ctx.with_message(msg)
    execution = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )
    prompt = SystemPrompt(
        content="",
        template_version="1.0",
        estimated_tokens=0,
        sections=(),
        metadata={},
    )
    return AgentRunResult(
        execution_result=execution,
        system_prompt=prompt,
        duration_seconds=1.0,
        agent_id="agent-001",
    )


@pytest.mark.unit
class TestCompletionSummary:
    """completion_summary returns last assistant message content."""

    def test_returns_last_assistant_content(self) -> None:
        result = _make_result_with_messages(
            ChatMessage(role=MessageRole.ASSISTANT, content="First"),
            ChatMessage(role=MessageRole.USER, content="Follow up"),
            ChatMessage(role=MessageRole.ASSISTANT, content="Final answer"),
        )
        assert result.completion_summary == "Final answer"

    def test_returns_none_when_no_assistant_messages(self) -> None:
        result = _make_result_with_messages(
            ChatMessage(role=MessageRole.USER, content="Hello"),
        )
        assert result.completion_summary is None

    def test_returns_none_for_empty_conversation(self) -> None:
        result = _make_result_with_messages()
        assert result.completion_summary is None

    def test_skips_tool_call_only_messages(self) -> None:
        """Assistant message with tool_calls but no content is skipped."""
        result = _make_result_with_messages(
            ChatMessage(role=MessageRole.ASSISTANT, content="Before tool"),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=None,
                tool_calls=(ToolCall(id="call-1", name="test_tool", arguments={}),),
            ),
        )
        assert result.completion_summary == "Before tool"

    def test_skips_empty_string_content(self) -> None:
        """Assistant message with empty string content is skipped."""
        result = _make_result_with_messages(
            ChatMessage(role=MessageRole.ASSISTANT, content="Real content"),
            ChatMessage(role=MessageRole.ASSISTANT, content=""),
        )
        assert result.completion_summary == "Real content"
