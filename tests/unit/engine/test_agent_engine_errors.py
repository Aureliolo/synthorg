"""Unit tests for AgentEngine error handling and edge cases."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.engine.task_execution import TaskExecution

    from .conftest import MockCompletionProvider

from .conftest import make_completion_response

pytestmark = pytest.mark.timeout(30)


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
            "synthorg.engine.agent_engine.build_system_prompt",
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
                "synthorg.engine.agent_engine.build_system_prompt",
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
                "synthorg.engine.agent_engine.build_system_prompt",
                side_effect=RecursionError("too deep"),
            ),
            pytest.raises(RecursionError),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
class TestAgentEngineMaxTurnsValidation:
    """max_turns < 1 raises ValueError at the engine boundary."""

    async def test_zero_max_turns_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ValueError, match="max_turns must be >= 1"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
                max_turns=0,
            )

    async def test_negative_max_turns_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ValueError, match="max_turns must be >= 1"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
                max_turns=-5,
            )


@pytest.mark.unit
class TestAgentEngineTimeoutValidation:
    """timeout_seconds <= 0 raises ValueError at the engine boundary."""

    @pytest.mark.parametrize(
        "timeout_val",
        [0, -1.0, -0.001],
        ids=["zero", "negative", "small_negative"],
    )
    async def test_invalid_timeout_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
        timeout_val: float,
    ) -> None:
        """Invalid timeout_seconds raises ValueError."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
                timeout_seconds=timeout_val,
            )


@pytest.mark.unit
class TestAgentEngineCostRecordingNonRecoverable:
    """MemoryError/RecursionError in _record_costs propagate unconditionally."""

    async def test_memory_error_in_cost_recording_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MemoryError from CostTracker.record() is not swallowed."""
        tracker = MagicMock()
        tracker.record = AsyncMock(side_effect=MemoryError("OOM in tracker"))
        response = make_completion_response(cost_usd=0.05)
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, cost_tracker=tracker)

        with pytest.raises(MemoryError, match="OOM in tracker"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

    async def test_recursion_error_in_cost_recording_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """RecursionError from CostTracker.record() is not swallowed."""
        tracker = MagicMock()
        tracker.record = AsyncMock(
            side_effect=RecursionError("infinite in tracker"),
        )
        response = make_completion_response(cost_usd=0.05)
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider, cost_tracker=tracker)

        with pytest.raises(RecursionError, match="infinite in tracker"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
class TestAgentEngineFatalErrorResult:
    """_handle_fatal_error result has correct structure."""

    async def test_error_result_has_error_message(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Errors in _handle_fatal_error path produce template_version='error'."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with patch(
            "synthorg.engine.agent_engine.build_system_prompt",
            side_effect=RuntimeError("LLM is down"),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.is_success is False
        assert "LLM is down" in (result.execution_result.error_message or "")
        assert result.agent_id == str(sample_agent_with_personality.id)
        assert result.task_id == sample_task_with_criteria.id
        assert result.system_prompt.template_version == "error"
        assert result.duration_seconds > 0

    async def test_handle_fatal_error_secondary_failure_raises_original(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """If _handle_fatal_error itself fails, original exception is raised."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with (
            patch(
                "synthorg.engine.agent_engine.build_system_prompt",
                side_effect=RuntimeError("original error"),
            ),
            patch(
                "synthorg.engine.agent_engine.AgentContext.from_identity",
                side_effect=ValueError("secondary failure"),
            ),
            pytest.raises(RuntimeError, match="original error") as exc_info,
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
        # raise exc from None suppresses the secondary error chain
        # so the original exception propagates cleanly
        assert exc_info.value.__cause__ is None


@pytest.mark.unit
class TestAgentEngineFatalErrorNonRecoverable:
    """MemoryError/RecursionError in _handle_fatal_error build path propagate."""

    async def test_memory_error_in_error_build_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MemoryError during error-result construction propagates directly."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with (
            patch(
                "synthorg.engine.agent_engine.build_system_prompt",
                side_effect=RuntimeError("trigger fatal path"),
            ),
            patch(
                "synthorg.engine.agent_engine.AgentContext.from_identity",
                side_effect=MemoryError("OOM in error build"),
            ),
            pytest.raises(MemoryError, match="OOM in error build"),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
class TestAgentEngineMemoryMessages:
    """Working memory messages injected into conversation."""

    async def test_memory_messages_in_context(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Memory messages appear between system prompt and task instruction."""
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
        mock_loop.get_loop_type = MagicMock(return_value="react")

        memory = (
            ChatMessage(role=MessageRole.USER, content="Previous context A"),
            ChatMessage(role=MessageRole.ASSISTANT, content="Previous response B"),
        )
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            memory_messages=memory,
        )

        # Verify context passed to loop has memory messages
        call_kwargs = mock_loop.execute.call_args.kwargs
        conversation = call_kwargs["context"].conversation
        contents = [m.content for m in conversation]
        # System prompt is first, then memory messages, then task instruction
        assert "Previous context A" in contents
        assert "Previous response B" in contents
        # Memory messages should appear after system prompt (index 0)
        sys_idx = next(
            i for i, m in enumerate(conversation) if m.role == MessageRole.SYSTEM
        )
        mem_idx = next(
            i for i, m in enumerate(conversation) if m.content == "Previous context A"
        )
        task_idx = next(
            i
            for i, m in enumerate(conversation)
            if m.role == MessageRole.USER and "# Task:" in m.content
        )
        assert sys_idx < mem_idx < task_idx


@pytest.mark.unit
class TestAgentEngineRecovery:
    """Recovery strategy is invoked on error outcomes."""

    async def test_provider_error_transitions_task_to_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Provider exception -> task status is FAILED."""
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM is down"))
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.ERROR
        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status is TaskStatus.FAILED

    async def test_recovery_strategy_invoked_on_failure(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Custom recovery strategy's recover() is called on failure."""
        mock_strategy = MagicMock(spec=FailAndReassignStrategy)
        # Use actual strategy for the real call, but track it
        real_strategy = FailAndReassignStrategy()
        mock_strategy.recover = AsyncMock(
            side_effect=real_strategy.recover,
        )
        mock_strategy.get_strategy_type = MagicMock(return_value="fail_reassign")

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("crash"))
        engine = AgentEngine(provider=provider, recovery_strategy=mock_strategy)

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        mock_strategy.recover.assert_called_once()

    async def test_recovery_failure_is_swallowed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """If recovery itself fails, engine still returns error result."""
        mock_strategy = MagicMock()
        mock_strategy.recover = AsyncMock(
            side_effect=ValueError("recovery broken"),
        )

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        engine = AgentEngine(provider=provider, recovery_strategy=mock_strategy)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # Engine still returns an error result, doesn't crash
        assert result.termination_reason == TerminationReason.ERROR
        assert result.is_success is False

    async def test_no_recovery_when_strategy_is_none(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Opting out of recovery: task stays IN_PROGRESS (not FAILED)."""
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("crash"))
        engine = AgentEngine(provider=provider, recovery_strategy=None)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.ERROR
        te = result.execution_result.context.task_execution
        assert te is not None
        # Without recovery, task stays at IN_PROGRESS (engine transitions
        # ASSIGNED->IN_PROGRESS before the loop runs)
        assert te.status is TaskStatus.IN_PROGRESS

    async def test_loop_timeout_triggers_recovery(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Wall-clock timeout -> ERROR -> recovery -> FAILED."""
        import asyncio

        async def slow_execute(**_kwargs: object) -> ExecutionResult:
            await asyncio.sleep(10)
            ctx = AgentContext.from_identity(
                sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
            return ExecutionResult(
                context=ctx,
                termination_reason=TerminationReason.COMPLETED,
            )

        mock_loop = MagicMock()
        mock_loop.execute = AsyncMock(side_effect=slow_execute)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            execution_loop=mock_loop,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
            timeout_seconds=0.01,
        )

        assert result.termination_reason == TerminationReason.ERROR
        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status is TaskStatus.FAILED

    async def test_custom_recovery_strategy_used(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Engine uses the custom strategy, not the default."""
        custom_results: list[str] = []

        class CustomRecovery:
            async def recover(
                self,
                *,
                task_execution: TaskExecution,
                error_message: str,
                context: AgentContext,
            ) -> RecoveryResult:
                custom_results.append("custom_called")
                real = FailAndReassignStrategy()
                return await real.recover(
                    task_execution=task_execution,
                    error_message=error_message,
                    context=context,
                )

            async def finalize(self, execution_id: str) -> None:
                pass

            def get_strategy_type(self) -> str:
                return "custom"

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("crash"))
        engine = AgentEngine(
            provider=provider,
            recovery_strategy=CustomRecovery(),
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert custom_results == ["custom_called"]

    async def test_memory_error_in_recovery_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """MemoryError from recovery strategy is not swallowed."""
        mock_strategy = MagicMock()
        mock_strategy.recover = AsyncMock(side_effect=MemoryError("OOM"))

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("crash"))
        engine = AgentEngine(
            provider=provider,
            recovery_strategy=mock_strategy,
        )

        with pytest.raises(MemoryError, match="OOM"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

    async def test_recursion_error_in_recovery_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """RecursionError from recovery strategy is not swallowed."""
        mock_strategy = MagicMock()
        mock_strategy.recover = AsyncMock(
            side_effect=RecursionError("max depth"),
        )

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("crash"))
        engine = AgentEngine(
            provider=provider,
            recovery_strategy=mock_strategy,
        )

        with pytest.raises(RecursionError, match="max depth"):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
