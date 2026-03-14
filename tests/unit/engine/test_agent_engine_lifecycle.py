"""Unit tests for AgentEngine post-execution transitions, timeout, and metrics."""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

from .conftest import make_completion_response as _make_completion_response

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestAgentEnginePostExecutionTransitions:
    """Post-execution task transitions based on termination reason."""

    async def test_completed_auto_completes_task(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """COMPLETED → two-hop: IP → IR → COMPLETED."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.COMPLETED

    async def test_completed_transition_log_has_three_entries(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """ASSIGNED→IP, IP→IR, IR→COMPLETED = 3 transitions."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert len(te.transition_log) == 3
        assert te.transition_log[0].from_status == TaskStatus.ASSIGNED
        assert te.transition_log[0].to_status == TaskStatus.IN_PROGRESS
        assert te.transition_log[1].from_status == TaskStatus.IN_PROGRESS
        assert te.transition_log[1].to_status == TaskStatus.IN_REVIEW
        assert te.transition_log[2].from_status == TaskStatus.IN_REVIEW
        assert te.transition_log[2].to_status == TaskStatus.COMPLETED

    async def test_completed_sets_completed_at(
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

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.completed_at is not None

    async def test_max_turns_stays_in_progress(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        # Simulate ASSIGNED→IP transition that _prepare_context does
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.MAX_TURNS,
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.IN_PROGRESS

    async def test_budget_exhausted_stays_in_progress(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
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
            termination_reason=TerminationReason.BUDGET_EXHAUSTED,
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.IN_PROGRESS

    async def test_error_transitions_to_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
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
            error_message="something failed",
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.FAILED

    async def test_shutdown_transitions_to_interrupted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """SHUTDOWN → task transitions to INTERRUPTED."""
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
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.INTERRUPTED

    async def test_shutdown_from_assigned_transitions_to_interrupted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """SHUTDOWN before loop starts → ASSIGNED → INTERRUPTED."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        # Simulate the loop returning SHUTDOWN while still ASSIGNED
        # (edge case: shutdown signal between assignment and IP transition)
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.SHUTDOWN,
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        # The engine's _prepare_context transitions ASSIGNED→IP,
        # but the mock loop returns a context still at ASSIGNED.
        # The engine's _apply_post_execution_transitions handles this.
        assert te.status == TaskStatus.INTERRUPTED

    async def test_no_task_execution_passes_through(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """No task_execution in context → transitions skipped."""
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        mock_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.execution_result.context.task_execution is None


@pytest.mark.unit
class TestAgentEngineTimeout:
    """Wall-clock timeout support."""

    async def test_timeout_produces_error_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Slow provider triggers timeout → ERROR result."""

        async def slow_execute(**kwargs: Any) -> ExecutionResult:
            await asyncio.sleep(10)
            msg = "Should not reach here"
            raise AssertionError(msg)

        mock_loop = MagicMock()
        mock_loop.execute = slow_execute
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            timeout_seconds=0.1,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.is_success is False
        assert "timeout" in (result.execution_result.error_message or "").lower()

    async def test_no_timeout_by_default(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Default run has no timeout."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True

    @pytest.mark.parametrize(
        "timeout_seconds",
        [0, -1.0],
        ids=["zero", "negative"],
    )
    async def test_non_positive_timeout_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
        *,
        timeout_seconds: float,
    ) -> None:
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
                timeout_seconds=timeout_seconds,
            )


@pytest.mark.unit
class TestAgentEngineCompletionMetrics:
    """Proxy overhead metrics logged on completion."""

    async def test_metrics_logged_on_completion(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Successful run computes and logs TaskCompletionMetrics."""
        from synthorg.engine.metrics import TaskCompletionMetrics

        response = _make_completion_response(
            input_tokens=400,
            output_tokens=200,
            cost_usd=0.05,
        )
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # Metrics can be computed from the result
        metrics = TaskCompletionMetrics.from_run_result(result)
        assert metrics.turns_per_task == 1
        assert metrics.tokens_per_task > 0
        assert metrics.cost_per_task > 0
        assert metrics.duration_seconds > 0
        assert metrics.agent_id == str(sample_agent_with_personality.id)
        assert metrics.task_id == sample_task_with_criteria.id
        assert 0.0 <= metrics.prompt_token_ratio <= 1.0


@pytest.mark.unit
class TestAgentEngineTimeoutEdgeCases:
    """Edge cases for timeout behaviour."""

    async def test_inner_timeout_propagates_without_engine_timeout(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """TimeoutError from inside the loop is treated as a fatal error."""

        async def raises_timeout(**kwargs: Any) -> ExecutionResult:
            msg = "inner timeout"
            raise TimeoutError(msg)

        mock_loop = MagicMock()
        mock_loop.execute = raises_timeout
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert "TimeoutError" in (result.execution_result.error_message or "")

    async def test_timeout_records_no_costs(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Timeout result has no turns, so no costs are recorded."""

        async def slow_execute(**kwargs: Any) -> ExecutionResult:
            await asyncio.sleep(10)
            msg = "Should not reach here"
            raise AssertionError(msg)

        mock_tracker = MagicMock()
        mock_tracker.record = AsyncMock()

        mock_loop = MagicMock()
        mock_loop.execute = slow_execute
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
            cost_tracker=mock_tracker,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            timeout_seconds=0.1,
        )

        assert result.termination_reason == TerminationReason.ERROR
        mock_tracker.record.assert_not_called()


@pytest.mark.unit
class TestAgentEnginePostExecutionResilience:
    """Post-execution transition failure resilience."""

    async def test_transition_failure_preserves_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Transition failure preserves execution result unchanged."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        te = ctx.task_execution
        assert te is not None
        bad_te = te.model_copy(update={"status": TaskStatus.CANCELLED})
        ctx_bad = ctx.model_copy(update={"task_execution": bad_te})

        mock_result = ExecutionResult(
            context=ctx_bad,
            termination_reason=TerminationReason.COMPLETED,
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.CANCELLED

    async def test_interrupted_transition_failure_preserves_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """SHUTDOWN with invalid task status → transition fails, result kept."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
        )
        te = ctx.task_execution
        assert te is not None
        # Force into COMPLETED (terminal) — INTERRUPTED transition should fail
        bad_te = te.model_copy(update={"status": TaskStatus.COMPLETED})
        ctx_bad = ctx.model_copy(update={"task_execution": bad_te})

        mock_result = ExecutionResult(
            context=ctx_bad,
            termination_reason=TerminationReason.SHUTDOWN,
        )
        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(return_value=mock_result)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        # Transition failed, so status stays as COMPLETED
        assert te.status == TaskStatus.COMPLETED
