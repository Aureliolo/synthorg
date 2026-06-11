"""Unit tests for AgentEngine orchestrator."""

import copy
from typing import TYPE_CHECKING, Any, override
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
import structlog.testing

from synthorg.budget.coordination_config import ErrorTaxonomyConfig
from synthorg.budget.tracker import CostTracker
from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import (
    ExecutionStateError,
    TaskEngineError,
)
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.execution.turn import TurnRecord
from synthorg.hr.enums import AgentStatus
from synthorg.observability.correlation import (
    bind_correlation_id,
    clear_correlation_ids,
)
from synthorg.observability.events.prompt import PROMPT_TOKEN_RATIO_HIGH
from tests._shared import JsonDict, as_uuid, mock_of

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.task_engine import TaskEngine

from .conftest import make_completion_response as _make_completion_response


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
        assert result.task_id == str(sample_task_with_criteria.id)

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

        # Successful run parks at IN_REVIEW: ASSIGNED -> IP -> IR
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW


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
        # Successful run parks at IN_REVIEW: IP -> IR
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW


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
            id=as_uuid("task-done"),
            title="Already done",
            description="This task is completed.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
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
            id=as_uuid("task-new"),
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

    async def test_blocked_task_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A BLOCKED task cannot be executed."""
        blocked_task = Task(
            id=as_uuid("task-blocked"),
            title="Blocked task",
            description="This task is blocked.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            status=TaskStatus.BLOCKED,
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ExecutionStateError, match="blocked"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=blocked_task,
            )

    async def test_task_assigned_to_different_agent_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A task assigned to another agent cannot be executed."""
        other_task = Task(
            id=as_uuid("task-other"),
            title="Other agent task",
            description="Assigned to someone else.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to="completely-different-agent-id",
            status=TaskStatus.ASSIGNED,
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ExecutionStateError, match="not to agent"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=other_task,
            )


@pytest.mark.unit
class TestAgentEngineMaxTurnsBoundary:
    """max_turns=1 is the minimum valid value."""

    async def test_max_turns_one_succeeds(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """max_turns=1 allows exactly one LLM turn."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            max_turns=1,
        )

        assert result.is_success is True
        assert result.total_turns == 1


@pytest.mark.unit
class TestAgentEngineWithTools:
    """Tools passed through to loop, tool calls work."""

    async def test_tools_from_registry(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from synthorg.security.autonomy.enums import ToolCategory
        from synthorg.tools.base import BaseTool, ToolExecutionResult
        from synthorg.tools.registry import ToolRegistry

        class EchoTool(BaseTool):
            @override
            async def execute(
                self,
                *,
                arguments: dict[str, object],
            ) -> ToolExecutionResult:
                return ToolExecutionResult(content=str(arguments))

        registry = ToolRegistry(
            [
                EchoTool(
                    name="echo",
                    description="Echoes input.",
                    category=ToolCategory.CODE_EXECUTION,
                ),
            ]
        )
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
        # D22: tools section is no longer in the default template.
        assert "tools" not in result.system_prompt.sections


@pytest.mark.unit
class TestAgentEngineMemoryToolWiring:
    """memory_injection_strategy wires memory tools into _make_tool_invoker."""

    def test_memory_tools_registered_when_strategy_provided(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from synthorg.memory.injection import InjectionStrategy
        from synthorg.memory.retrieval_config import MemoryRetrievalConfig
        from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
        from synthorg.security.autonomy.enums import ToolCategory
        from synthorg.tools.base import BaseTool, ToolExecutionResult
        from synthorg.tools.registry import ToolRegistry

        class StubTool(BaseTool):
            @override
            async def execute(
                self,
                *,
                arguments: dict[str, object],
            ) -> ToolExecutionResult:
                return ToolExecutionResult(content="stub")

        registry = ToolRegistry(
            [StubTool(name="stub", category=ToolCategory.OTHER)],
        )
        backend = AsyncMock()
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(
                strategy=InjectionStrategy.TOOL_BASED,
            ),
        )
        provider = mock_provider_factory(
            [_make_completion_response()],
        )
        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
            memory_injection_strategy=strategy,
        )

        invoker = engine._make_tool_invoker(
            sample_agent_with_personality,
        )

        assert invoker is not None
        assert "search_memory" in [d.name for d in invoker.get_permitted_definitions()]
        assert "recall_memory" in [d.name for d in invoker.get_permitted_definitions()]

    def test_no_memory_tools_when_strategy_is_none(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from synthorg.security.autonomy.enums import ToolCategory
        from synthorg.tools.base import BaseTool, ToolExecutionResult
        from synthorg.tools.registry import ToolRegistry

        class StubTool(BaseTool):
            @override
            async def execute(
                self,
                *,
                arguments: dict[str, object],
            ) -> ToolExecutionResult:
                return ToolExecutionResult(content="stub")

        registry = ToolRegistry(
            [StubTool(name="stub", category=ToolCategory.OTHER)],
        )
        provider = mock_provider_factory(
            [_make_completion_response()],
        )
        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
        )

        invoker = engine._make_tool_invoker(
            sample_agent_with_personality,
        )

        assert invoker is not None
        names = [d.name for d in invoker.get_permitted_definitions()]
        assert "search_memory" not in names
        assert "recall_memory" not in names


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
        mock_loop = mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="react"),
        )

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
            id=as_uuid("task-no-budget"),
            title="No budget limit",
            description="A task with no budget.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            budget_limit=0.0,
            status=TaskStatus.ASSIGNED,
        )
        response = _make_completion_response(cost=100.0)
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
        response = _make_completion_response(cost=0.05)
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
            id=as_uuid("task-free"),
            title="Free task",
            description="Zero cost run.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            status=TaskStatus.ASSIGNED,
        )
        response = _make_completion_response(
            cost=0.0,
            input_tokens=0,
            output_tokens=0,
        )
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, cost_tracker=tracker)

        await engine.run(identity=sample_agent_with_personality, task=task)

        count = await tracker.get_record_count()
        assert count == 0

    async def test_free_provider_tokens_recorded(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Free provider: cost=0 but tokens>0 -> record IS created."""
        tracker = CostTracker()
        task = Task(
            id=as_uuid("task-free-tokens"),
            title="Free with tokens",
            description="Zero cost but nonzero tokens.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            status=TaskStatus.ASSIGNED,
        )
        response = _make_completion_response(
            cost=0.0,
            input_tokens=5,
            output_tokens=2,
        )
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, cost_tracker=tracker)

        await engine.run(identity=sample_agent_with_personality, task=task)

        count = await tracker.get_record_count()
        assert count == 1

    async def test_cost_tracker_failure_preserves_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """CostTracker.record() failure does not affect execution result."""
        tracker = MagicMock(spec=CostTracker)
        tracker.budget_config = None
        tracker.record = AsyncMock(side_effect=RuntimeError("DB write failed"))
        response = _make_completion_response(cost=0.05)
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
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="custom"),
        )

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
class TestAgentEngineDefaultLoop:
    """No loop specified -> ReactLoop used."""

    async def test_default_is_react_loop(
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
        # Verify through observable behavior: the result metadata
        # confirms the default loop ran successfully
        assert result.is_success is True

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
        # Mock must return context at IN_PROGRESS (as _prepare_context
        # transitions ASSIGNED → IP before handing to the loop).
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="custom"),
        )

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

    def test_stagnation_detector_wired_to_default_loop(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Stagnation detector is passed to the default ReactLoop."""
        from synthorg.engine.react_loop import ReactLoop
        from synthorg.engine.stagnation import ToolRepetitionDetector

        detector = ToolRepetitionDetector()
        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            stagnation_detector=detector,
        )
        loop = engine._loop
        assert isinstance(loop, ReactLoop)
        assert loop.stagnation_detector is detector


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
class TestAgentEngineClassification:
    """Error taxonomy classification integration."""

    async def test_no_config_skips_classification(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """With error_taxonomy_config=None, classification is not called."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            error_taxonomy_config=None,
        )

        with patch(
            "synthorg.engine.agent_engine_post_exec.classify_execution_errors",
        ) as mock_classify:
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        mock_classify.assert_not_called()
        assert result.is_success is True

    async def test_enabled_config_calls_classification(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """With a config, classify_execution_errors is invoked."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        config = ErrorTaxonomyConfig(enabled=True)
        engine = AgentEngine(
            provider=provider,
            error_taxonomy_config=config,
        )

        with patch(
            "synthorg.engine.agent_engine_post_exec.classify_execution_errors",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_classify:
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        mock_classify.assert_called_once()
        assert result.is_success is True

    async def test_classification_memory_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MemoryError from classification propagates unconditionally."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        config = ErrorTaxonomyConfig(enabled=True)
        engine = AgentEngine(
            provider=provider,
            error_taxonomy_config=config,
        )

        with (
            patch(
                "synthorg.engine.agent_engine_post_exec.classify_execution_errors",
                new_callable=AsyncMock,
                side_effect=MemoryError,
            ),
            pytest.raises(MemoryError),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
class TestAgentEnginePromptTokenRatioWarning:
    """High prompt-to-total token ratio emits PROMPT_TOKEN_RATIO_HIGH."""

    @pytest.mark.parametrize(
        (
            "prompt_tokens",
            "input_tokens",
            "output_tokens",
            "cost",
            "expect_warning",
        ),
        [
            # prompt_tokens=200 out of 400 total → ratio 0.50 > 0.3 threshold.
            (200, 300, 100, 0.01, True),
            # prompt_tokens=50 out of 10000 total → ratio 0.005 < 0.3 threshold.
            (50, 5000, 5000, 1.0, False),
        ],
        ids=["high_ratio", "low_ratio"],
    )
    async def test_prompt_token_ratio_warning(  # noqa: PLR0913
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
        *,
        prompt_tokens: int,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        expect_warning: bool,
    ) -> None:
        """Warning emitted iff prompt tokens dominate total tokens.

        Injects a fixed ``estimated_tokens`` via mock to isolate the
        threshold-check logic from the live prompt estimator.
        """
        from synthorg.engine.prompt import SystemPrompt

        response = _make_completion_response(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        fixed_prompt = SystemPrompt(
            content="test",
            template_version="test",
            estimated_tokens=prompt_tokens,
            sections=("identity",),
            metadata={"agent_id": str(sample_agent_with_personality.id)},
        )

        with (
            patch(
                "synthorg.engine.agent_engine_context.build_system_prompt",
                return_value=fixed_prompt,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        warning_events = [e for e in logs if e.get("event") == PROMPT_TOKEN_RATIO_HIGH]
        if expect_warning:
            assert len(warning_events) == 1
            assert "prompt_token_ratio" in warning_events[0]
        else:
            assert len(warning_events) == 0


def _make_sync_success(
    request_id: str = "test",
    version: int = 1,
) -> TaskMutationResult:
    """Build a successful TaskMutationResult for sync tests."""
    return TaskMutationResult(
        request_id=request_id,
        success=True,
        version=version,
    )


def _make_sync_failure(
    request_id: str = "test",
    error: str = "rejected",
) -> TaskMutationResult:
    """Build a failed TaskMutationResult for sync tests."""
    return TaskMutationResult(
        request_id=request_id,
        success=False,
        error=error,
        error_code="validation",
    )


@pytest.mark.unit
class TestSyncToTaskEngine:
    """Tests for incremental TaskEngine status sync."""

    async def test_no_task_engine_is_noop(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Without task_engine, run() succeeds and no syncing occurs."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, task_engine=None)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True

    async def test_completed_path_produces_two_syncs(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """COMPLETED path syncs IN_PROGRESS then IN_REVIEW (awaits review)."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(return_value=_make_sync_success())

        engine = AgentEngine(provider=provider, task_engine=mock_te)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True
        assert mock_te.submit.await_count == 2
        synced_statuses = [
            call.args[0].target_status for call in mock_te.submit.call_args_list
        ]
        assert synced_statuses == [
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
        ]

    async def test_shutdown_path_produces_two_syncs(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """SHUTDOWN path syncs IN_PROGRESS then INTERRUPTED."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.SHUTDOWN,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="react"),
        )

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(return_value=_make_sync_success())

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
            task_engine=mock_te,
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert mock_te.submit.await_count == 2
        synced_statuses = [
            call.args[0].target_status for call in mock_te.submit.call_args_list
        ]
        assert synced_statuses == [
            TaskStatus.IN_PROGRESS,
            TaskStatus.INTERRUPTED,
        ]

    async def test_error_path_produces_two_syncs(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """ERROR path syncs IN_PROGRESS then FAILED (after recovery)."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.ERROR,
            error_message="something broke",
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="react"),
        )

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(return_value=_make_sync_success())

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
            task_engine=mock_te,
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert mock_te.submit.await_count == 2
        synced_statuses = [
            call.args[0].target_status for call in mock_te.submit.call_args_list
        ]
        assert synced_statuses == [
            TaskStatus.IN_PROGRESS,
            TaskStatus.FAILED,
        ]

    async def test_max_turns_syncs_only_in_progress(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MAX_TURNS path: only IN_PROGRESS is synced (no final transition)."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.MAX_TURNS,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="react"),
        )

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(return_value=_make_sync_success())

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
            task_engine=mock_te,
            recovery_strategy=None,
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert mock_te.submit.await_count == 1
        assert (
            mock_te.submit.call_args_list[0].args[0].target_status
            == TaskStatus.IN_PROGRESS
        )

    async def test_sync_failure_isolated_from_subsequent_transitions(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A failed sync does not block subsequent transitions."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])

        # First call (IN_PROGRESS) fails, rest succeed
        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(
            side_effect=[
                _make_sync_failure(),
                _make_sync_success(),
            ],
        )

        engine = AgentEngine(provider=provider, task_engine=mock_te)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # Run still succeeds despite first sync failure
        assert result.is_success is True
        assert mock_te.submit.await_count == 2

    async def test_task_engine_error_swallowed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """TaskEngineError from submit() is logged and swallowed."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(
            side_effect=TaskEngineError("engine unavailable"),
        )

        engine = AgentEngine(provider=provider, task_engine=mock_te)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True

    async def test_unexpected_error_swallowed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Unexpected Exception from submit() is logged and swallowed."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(
            side_effect=RuntimeError("connection lost"),
        )

        engine = AgentEngine(provider=provider, task_engine=mock_te)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True

    async def test_memory_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MemoryError from submit() is re-raised, not swallowed."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(
            side_effect=MemoryError("out of memory"),
        )

        engine = AgentEngine(provider=provider, task_engine=mock_te)

        with pytest.raises(MemoryError, match="out of memory"):
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
        """RecursionError from submit() is re-raised, not swallowed."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])

        mock_te = MagicMock(spec=TaskEngine)
        mock_te.submit = AsyncMock(
            side_effect=RecursionError("maximum recursion depth exceeded"),
        )

        engine = AgentEngine(provider=provider, task_engine=mock_te)

        with pytest.raises(RecursionError, match="maximum recursion depth exceeded"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
def test_snapshot_channel_matches_api_channel() -> None:
    """TaskEngine._SNAPSHOT_CHANNEL must match CHANNEL_TASKS in api.channels."""
    from synthorg.api.channels import CHANNEL_TASKS
    from synthorg.engine.task_engine import TaskEngine

    assert TaskEngine._SNAPSHOT_CHANNEL == CHANNEL_TASKS


@pytest.mark.unit
class TestAgentEngineCoordinator:
    """Tests for coordinator property and coordinate() method."""

    def test_coordinator_default_is_none(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)
        assert engine.coordinator is None

    def test_coordinator_property_returns_coordinator(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        mock_coordinator = MagicMock(spec=MultiAgentCoordinator)
        engine = AgentEngine(provider=provider, coordinator=mock_coordinator)
        assert engine.coordinator is mock_coordinator

    async def test_coordinate_raises_when_no_coordinator(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)
        mock_context = MagicMock()
        with pytest.raises(ExecutionStateError, match="No coordinator configured"):
            await engine.coordinate(mock_context)

    async def test_coordinate_delegates_to_coordinator(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        mock_coordinator = AsyncMock(spec=MultiAgentCoordinator)
        expected_result = MagicMock()
        mock_coordinator.coordinate.return_value = expected_result

        engine = AgentEngine(provider=provider, coordinator=mock_coordinator)
        mock_context = MagicMock()
        result = await engine.coordinate(mock_context)

        assert result is expected_result
        mock_coordinator.coordinate.assert_awaited_once_with(mock_context)


@pytest.mark.unit
class TestAgentEngineCorrelationBinding:
    """Verify correlation IDs are bound/unbound around run()."""

    async def test_run_uses_correlation_scope(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """run() wraps execution in correlation_scope."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        with patch(
            "synthorg.engine.agent_engine.correlation_scope",
        ) as mock_scope:
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

            mock_scope.assert_called_once_with(
                agent_id=str(sample_agent_with_personality.id),
                task_id=str(sample_task_with_criteria.id),
                project_id=sample_task_with_criteria.project,
            )

    async def test_correlation_context_clean_after_error(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Context is clean after execution fails (scoped binding)."""
        # first complete() call fails and engine returns error result
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        try:
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
            ctx = structlog.contextvars.get_contextvars()
            assert "agent_id" not in ctx
            assert "task_id" not in ctx
        finally:
            clear_correlation_ids()

    async def test_correlation_scope_not_entered_on_validation_error(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Validation happens before binding, so correlation_scope
        is not entered when validation raises.
        """
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        with patch(
            "synthorg.engine.agent_engine.correlation_scope",
        ) as mock_scope:
            with pytest.raises(ValueError, match="max_turns"):
                await engine.run(
                    identity=sample_agent_with_personality,
                    task=sample_task_with_criteria,
                    max_turns=0,
                )
            mock_scope.assert_not_called()

    async def test_structlog_context_carries_correlation(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Correlation IDs visible during execution, parent context preserved."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        # Pre-bind a request_id to verify parent context is preserved
        bind_correlation_id(request_id="test-request-123")

        captured_ctx: JsonDict = {}
        original_complete = provider.complete

        async def capturing_complete(*args: Any, **kwargs: Any) -> Any:  # type: ignore[explicit-any]  # forwards verbatim to provider.complete
            captured_ctx.update(structlog.contextvars.get_contextvars())
            return await original_complete(*args, **kwargs)

        provider.complete = capturing_complete  # type: ignore[method-assign]

        try:
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
        finally:
            # Scoped binding should restore parent context
            ctx = structlog.contextvars.get_contextvars()
            assert "agent_id" not in ctx
            assert "task_id" not in ctx
            assert ctx.get("request_id") == "test-request-123"
            clear_correlation_ids()

        # Correlation IDs were present during execution
        assert captured_ctx.get("agent_id") == str(
            sample_agent_with_personality.id,
        )
        assert captured_ctx.get("task_id") == str(sample_task_with_criteria.id)
        assert captured_ctx.get("request_id") == "test-request-123"
