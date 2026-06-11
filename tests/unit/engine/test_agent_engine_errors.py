"""Unit tests for AgentEngine error handling and edge cases."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.engine.task_execution import TaskExecution

    from .conftest import MockCompletionProvider

from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.recovery import RecoveryStrategy
from synthorg.providers.protocol import CompletionProvider

from .conftest import make_completion_response


@pytest.mark.unit
class TestAgentEngineErrorHandling:
    """Provider exceptions -> error result (not crash)."""

    async def test_provider_error_returns_error_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        provider = MagicMock(spec=CompletionProvider)
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
            "synthorg.engine.agent_engine_context.build_system_prompt",
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
                "synthorg.engine.agent_engine_context.build_system_prompt",
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
                "synthorg.engine.agent_engine_context.build_system_prompt",
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
        tracker = MagicMock(spec=CostTracker)
        tracker.record = AsyncMock(side_effect=MemoryError("OOM in tracker"))
        # Explicit None triggers DEFAULT_CURRENCY fallback in cost_recording;
        # without it MagicMock.budget_config leaks a MagicMock currency.
        tracker.budget_config = None
        response = make_completion_response(cost=0.05)
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
        tracker = MagicMock(spec=CostTracker)
        tracker.record = AsyncMock(
            side_effect=RecursionError("infinite in tracker"),
        )
        # See TestAgentEngineCostRecordingNonRecoverable::test_memory_error_*
        # for why budget_config must be None rather than a MagicMock default.
        tracker.budget_config = None
        response = make_completion_response(cost=0.05)
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
            "synthorg.engine.agent_engine_context.build_system_prompt",
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
        assert result.task_id == str(sample_task_with_criteria.id)
        assert result.system_prompt.template_version == "error"
        assert result.duration_seconds > 0

    async def test_error_message_with_path_is_sanitized(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Paths in exception messages are redacted in the error result."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)

        with patch(
            "synthorg.engine.agent_engine_context.build_system_prompt",
            side_effect=RuntimeError(r"Failed reading C:\Users\dev\secret.key"),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        error_msg = result.execution_result.error_message or ""
        assert "[REDACTED_PATH]" in error_msg
        assert "C:\\Users" not in error_msg

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
                "synthorg.engine.agent_engine_context.build_system_prompt",
                side_effect=RuntimeError("original error"),
            ),
            patch(
                "synthorg.engine.agent_engine_errors.AgentContext.from_identity",
                side_effect=ValueError("secondary failure"),
            ),
            pytest.raises(RuntimeError, match="original error") as exc_info,
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
        # exc.add_note() preserves the secondary failure info
        # without inverting the exception chain semantics
        assert hasattr(exc_info.value, "__notes__")
        assert any("secondary failure" in note for note in exc_info.value.__notes__)


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
                "synthorg.engine.agent_engine_context.build_system_prompt",
                side_effect=RuntimeError("trigger fatal path"),
            ),
            patch(
                "synthorg.engine.agent_engine_errors.AgentContext.from_identity",
                side_effect=MemoryError("OOM in error build"),
            ),
            pytest.raises(MemoryError, match="OOM in error build"),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )


@pytest.mark.unit
class TestAgentEngineBudgetErrorSecondaryFailure:
    """Secondary failure in _handle_budget_error preserves context via note."""

    async def test_handle_budget_error_secondary_failure_preserves_note(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """If _handle_budget_error itself fails, original exception has note."""
        provider = mock_provider_factory([])
        budget_enforcer = AsyncMock(spec=BudgetEnforcer)
        budget_enforcer.check_can_execute = AsyncMock(
            side_effect=BudgetExhaustedError("budget exceeded"),
        )
        engine = AgentEngine(
            provider=provider,
            budget_enforcer=budget_enforcer,
        )

        with (
            patch(
                "synthorg.engine.agent_engine_errors.AgentContext.from_identity",
                side_effect=ValueError("secondary failure"),
            ),
            pytest.raises(
                BudgetExhaustedError,
                match="budget exceeded",
            ) as exc_info,
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        assert hasattr(exc_info.value, "__notes__")
        assert any("secondary failure" in note for note in exc_info.value.__notes__)


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
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_loop = MagicMock(spec=ExecutionLoop)
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
            if m.role == MessageRole.USER and "# Task" in m.content
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
        provider = MagicMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM is down"))
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.ERROR
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status is TaskStatus.FAILED

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

        provider = MagicMock(spec=CompletionProvider)
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
        mock_strategy = MagicMock(spec=RecoveryStrategy)
        mock_strategy.recover = AsyncMock(
            side_effect=ValueError("recovery broken"),
        )

        provider = MagicMock(spec=CompletionProvider)
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
        provider = MagicMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=RuntimeError("crash"))
        engine = AgentEngine(provider=provider, recovery_strategy=None)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.ERROR
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        # Without recovery, task stays at IN_PROGRESS (engine transitions
        # ASSIGNED->IN_PROGRESS before the loop runs)
        assert task_execution.status is TaskStatus.IN_PROGRESS

    async def test_loop_timeout_triggers_recovery(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Wall-clock timeout -> ERROR -> recovery -> FAILED."""
        import asyncio

        async def slow_execute(**_kwargs: object) -> ExecutionResult:
            await asyncio.Event().wait()  # blocks until cancelled by timeout
            ctx = AgentContext.from_identity(
                sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
            return ExecutionResult(
                context=ctx,
                termination_reason=TerminationReason.COMPLETED,
            )

        mock_loop = MagicMock(spec=ExecutionLoop)
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
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status is TaskStatus.FAILED

    async def test_timeout_does_not_block_on_cancellation_ignoring_loop(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Timeout returns ERROR promptly even when the loop swallows cancel."""
        import asyncio

        release = asyncio.Event()

        async def cancellation_ignoring_execute(**_kwargs: object) -> ExecutionResult:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Swallow the cancellation and keep "working" until released;
                # the engine must not wait on this task past the wall clock.
                await release.wait()
            ctx = AgentContext.from_identity(
                sample_agent_with_personality,
                task=sample_task_with_criteria,
            )
            return ExecutionResult(
                context=ctx,
                termination_reason=TerminationReason.COMPLETED,
            )

        mock_loop = MagicMock(spec=ExecutionLoop)
        mock_loop.execute = AsyncMock(side_effect=cancellation_ignoring_execute)
        mock_loop.get_loop_type = MagicMock(return_value="react")

        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider, execution_loop=mock_loop)

        try:
            result = await asyncio.wait_for(
                engine.run(
                    identity=sample_agent_with_personality,
                    task=sample_task_with_criteria,
                    timeout_seconds=0.01,
                ),
                timeout=5.0,
            )
            assert result.termination_reason == TerminationReason.ERROR
        finally:
            # Release the orphaned loop task so it completes cleanly instead
            # of lingering until the event loop is torn down.
            release.set()
            await asyncio.sleep(0)

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

        provider = MagicMock(spec=CompletionProvider)
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
        mock_strategy = MagicMock(spec=RecoveryStrategy)
        mock_strategy.recover = AsyncMock(side_effect=MemoryError("OOM"))

        provider = MagicMock(spec=CompletionProvider)
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
        mock_strategy = MagicMock(spec=RecoveryStrategy)
        mock_strategy.recover = AsyncMock(
            side_effect=RecursionError("max depth"),
        )

        provider = MagicMock(spec=CompletionProvider)
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
