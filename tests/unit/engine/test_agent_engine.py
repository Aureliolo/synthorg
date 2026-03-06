"""Unit tests for AgentEngine and AgentRunResult."""

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_company.budget.tracker import CostTracker
from ai_company.core.agent import AgentIdentity  # noqa: TC001
from ai_company.core.enums import AgentStatus, Priority, TaskStatus, TaskType
from ai_company.core.task import Task
from ai_company.engine.agent_engine import AgentEngine, _format_task_instruction
from ai_company.engine.context import AgentContext
from ai_company.engine.errors import ExecutionStateError
from ai_company.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from ai_company.engine.run_result import AgentRunResult
from ai_company.providers.enums import FinishReason
from ai_company.providers.models import (
    CompletionResponse,
    TokenUsage,
)

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider


def _make_completion_response(
    *,
    content: str = "Done.",
    finish_reason: FinishReason = FinishReason.STOP,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.01,
) -> CompletionResponse:
    """Build a simple CompletionResponse for tests."""
    return CompletionResponse(
        content=content,
        finish_reason=finish_reason,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        ),
        model="test-model-001",
    )


@pytest.mark.unit
class TestAgentEngineBasicRun:
    """Happy path: identity + task -> successful result with metadata."""

    async def test_basic_run_returns_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert isinstance(result, AgentRunResult)
        assert result.agent_id == str(sample_agent_with_personality.id)
        assert result.task_id == sample_task_with_criteria.id

    async def test_basic_run_is_success(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestAgentEngineSystemPrompt:
    """System prompt is built and included in result."""

    async def test_system_prompt_in_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.system_prompt.content
        assert "identity" in result.system_prompt.sections
        assert result.system_prompt.metadata["agent_id"] == str(
            sample_agent_with_personality.id,
        )


@pytest.mark.unit
class TestAgentEngineTaskTransition:
    """ASSIGNED -> IN_PROGRESS transition on start."""

    async def test_assigned_transitions_to_in_progress(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        assert sample_task_with_criteria.status == TaskStatus.ASSIGNED
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # The context inside the result should show IN_PROGRESS
        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.IN_PROGRESS


@pytest.mark.unit
class TestAgentEngineAlreadyInProgress:
    """IN_PROGRESS task runs without transition."""

    async def test_in_progress_accepted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        task_ip = sample_task_with_criteria.with_transition(TaskStatus.IN_PROGRESS)
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=task_ip,
        )

        assert result.is_success is True
        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.IN_PROGRESS


@pytest.mark.unit
class TestAgentEngineInvalidInput:
    """Inactive agent, invalid task status -> error."""

    async def test_inactive_agent_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        inactive = sample_agent_with_personality.model_copy(
            update={"status": AgentStatus.ON_LEAVE},
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ExecutionStateError, match="on_leave"):
            await engine.run(identity=inactive, task=sample_task_with_criteria)

    async def test_terminated_agent_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        terminated = sample_agent_with_personality.model_copy(
            update={"status": AgentStatus.TERMINATED},
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ExecutionStateError, match="terminated"):
            await engine.run(identity=terminated, task=sample_task_with_criteria)

    async def test_completed_task_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A task already COMPLETED cannot be executed."""
        completed_task = Task(
            id="task-done",
            title="Already done",
            description="This task is completed.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.COMPLETED,
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ExecutionStateError, match="completed"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=completed_task,
            )

    async def test_created_task_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A task still in CREATED status (unassigned) cannot be executed."""
        created_task = Task(
            id="task-new",
            title="New task",
            description="Unassigned task.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            status=TaskStatus.CREATED,
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ExecutionStateError, match="created"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=created_task,
            )


@pytest.mark.unit
class TestAgentEngineWithTools:
    """Tools passed through to loop, tool calls work."""

    async def test_tools_from_registry(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from ai_company.tools.base import BaseTool, ToolExecutionResult
        from ai_company.tools.registry import ToolRegistry

        class EchoTool(BaseTool):
            async def execute(
                self,
                *,
                arguments: dict[str, Any],
            ) -> ToolExecutionResult:
                return ToolExecutionResult(content=str(arguments))

        registry = ToolRegistry([EchoTool(name="echo", description="Echoes input.")])
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True
        # System prompt should include tools section
        assert "tools" in result.system_prompt.sections


@pytest.mark.unit
class TestAgentEngineBudgetChecker:
    """Budget limit creates checker, exhaustion terminates."""

    async def test_budget_checker_passed_and_terminates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Budget limit > 0 creates checker and passes it to the loop."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.BUDGET_EXHAUSTED,
            turns=(),
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        call_kwargs = mock_loop.execute.call_args.kwargs
        assert call_kwargs["budget_checker"] is not None
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert result.is_success is False

    async def test_no_budget_limit_no_checker(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Task with budget_limit=0 should not create a budget checker."""
        task = Task(
            id="task-no-budget",
            title="No budget limit",
            description="A task with no budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            budget_limit=0.0,
            status=TaskStatus.ASSIGNED,
        )
        response = _make_completion_response(cost_usd=100.0)
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=task,
        )

        # Without budget checker, should complete normally
        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestAgentEngineCostRecording:
    """CostTracker.record() called with correct data."""

    async def test_cost_recorded(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        tracker = CostTracker()
        response = _make_completion_response(cost_usd=0.05)
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            cost_tracker=tracker,
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        count = await tracker.get_record_count()
        assert count == 1
        total = await tracker.get_total_cost()
        assert total > 0

    async def test_no_cost_recorded_without_tracker(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """No error when cost_tracker is None."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True

    async def test_zero_cost_not_recorded(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """CostTracker present but zero cost/tokens -> no record created."""
        tracker = CostTracker()
        task = Task(
            id="task-free",
            title="Free task",
            description="Zero cost run.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="someone",
            status=TaskStatus.ASSIGNED,
        )
        response = _make_completion_response(
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
        )
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, cost_tracker=tracker)

        await engine.run(identity=sample_agent_with_personality, task=task)

        count = await tracker.get_record_count()
        assert count == 0

    async def test_cost_tracker_failure_preserves_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """CostTracker.record() failure does not affect execution result."""
        tracker = MagicMock()
        tracker.record = AsyncMock(side_effect=RuntimeError("DB write failed"))
        response = _make_completion_response(cost_usd=0.05)
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, cost_tracker=tracker)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestAgentEngineCompletionConfig:
    """completion_config is forwarded to the execution loop."""

    async def test_completion_config_forwarded(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A provided CompletionConfig reaches the execution loop."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="custom")

        config = MagicMock()
        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            completion_config=config,
        )

        call_kwargs = mock_loop.execute.call_args.kwargs
        assert call_kwargs["completion_config"] is config


@pytest.mark.unit
class TestAgentEngineMaxTurns:
    """max_turns parameter is forwarded to the execution context."""

    async def test_max_turns_forwarded(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Custom max_turns value is propagated to the context."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            max_turns=5,
        )

        assert result.execution_result.context.max_turns == 5


@pytest.mark.unit
class TestAgentEngineErrorHandling:
    """Provider exceptions -> error result (not crash)."""

    async def test_provider_error_returns_error_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM is down"))
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # The error should be caught at either the loop or engine level
        assert result.termination_reason == TerminationReason.ERROR
        assert result.is_success is False

    async def test_prompt_build_error_returns_error_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with patch(
            "ai_company.engine.agent_engine.build_system_prompt",
            side_effect=RuntimeError("template broken"),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.ERROR
        assert "template broken" in (result.execution_result.error_message or "")


@pytest.mark.unit
class TestAgentEngineNonRecoverable:
    """MemoryError/RecursionError propagate."""

    async def test_memory_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with (
            patch(
                "ai_company.engine.agent_engine.build_system_prompt",
                side_effect=MemoryError("out of memory"),
            ),
            pytest.raises(MemoryError),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

    async def test_recursion_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with (
            patch(
                "ai_company.engine.agent_engine.build_system_prompt",
                side_effect=RecursionError("too deep"),
            ),
            pytest.raises(RecursionError),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
class TestAgentEngineDuration:
    """duration_seconds > 0 in result."""

    async def test_duration_is_positive(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.duration_seconds > 0


@pytest.mark.unit
class TestAgentEngineConvenienceFields:
    """Computed fields on AgentRunResult delegate correctly."""

    async def test_total_turns(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.total_turns == 1

    async def test_total_cost_usd(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response(cost_usd=0.05)
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.total_cost_usd == pytest.approx(0.05)


@pytest.mark.unit
class TestAgentEngineDefaultLoop:
    """No loop specified -> ReactLoop used."""

    async def test_default_is_react_loop(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from ai_company.engine.react_loop import ReactLoop

        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        # Access the private loop to verify type
        assert isinstance(engine._loop, ReactLoop)

    async def test_custom_loop_used(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A custom ExecutionLoop is used when provided."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="custom")

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True
        mock_loop.execute.assert_awaited_once()


@pytest.mark.unit
class TestAgentEngineImmutability:
    """Original identity/task unchanged after run."""

    async def test_identity_unchanged(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        identity_before = copy.deepcopy(sample_agent_with_personality)
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert sample_agent_with_personality == identity_before

    async def test_task_unchanged(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        task_before = copy.deepcopy(sample_task_with_criteria)
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # Original task status should still be ASSIGNED
        assert sample_task_with_criteria.status == TaskStatus.ASSIGNED
        assert sample_task_with_criteria == task_before


@pytest.mark.unit
class TestFormatTaskInstruction:
    """Test _format_task_instruction helper."""

    def test_basic_format(self, sample_task_with_criteria: Task) -> None:
        result = _format_task_instruction(sample_task_with_criteria)

        assert "# Task: Implement authentication module" in result
        assert "JWT-based authentication" in result
        assert "## Acceptance Criteria" in result
        assert "- Login endpoint returns JWT token" in result
        assert "$5.00 USD" in result

    def test_deadline_included(self, sample_task_with_criteria: Task) -> None:
        result = _format_task_instruction(sample_task_with_criteria)

        assert "**Deadline:** 2026-04-01T00:00:00" in result

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
        result = _format_task_instruction(task)

        assert "# Task: Simple task" in result
        assert "Do the thing." in result
        assert "Acceptance Criteria" not in result
        assert "Budget" not in result
