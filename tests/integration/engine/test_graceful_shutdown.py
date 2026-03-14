"""Integration test — full graceful shutdown flow.

Creates an engine with a shutdown manager, starts an agent, triggers
shutdown, and verifies: agent stops, task is INTERRUPTED, cleanup runs.
"""

from typing import Any

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.shutdown import (
    CooperativeTimeoutStrategy,
    ShutdownManager,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.timeout(30)


class _ShutdownTriggeringProvider:
    """Provider that triggers shutdown on the first call.

    The first call triggers shutdown and returns a STOP response,
    so the loop completes before checking the shutdown flag again.
    """

    def __init__(self, strategy: CooperativeTimeoutStrategy) -> None:
        self._strategy = strategy
        self._call_count = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self._call_count += 1
        if self._call_count == 1:
            # Trigger shutdown after first LLM call
            self._strategy.request_shutdown()
        return CompletionResponse(
            content="Working on it.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=50,
                output_tokens=25,
                cost_usd=0.005,
            ),
            model="test-model-001",
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> Any:
        msg = "Not implemented"
        raise NotImplementedError(msg)

    async def get_model_capabilities(self, model: str) -> Any:
        from synthorg.providers.capabilities import ModelCapabilities

        return ModelCapabilities(
            model_id=model,
            provider="test-provider",
            supports_tools=False,
            supports_streaming=False,
            max_context_tokens=8192,
            max_output_tokens=4096,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        )


@pytest.mark.integration
class TestGracefulShutdownFlow:
    """Full shutdown integration: engine + strategy + agent → INTERRUPTED."""

    async def test_shutdown_signal_propagates_through_manager(
        self,
    ) -> None:
        """Shutdown signal during execution propagates through manager.

        The provider triggers shutdown on the first call but returns
        STOP (no tool calls), so the loop completes *before* the next
        shutdown check.  This verifies the signal propagation path —
        test_shutdown_during_multi_turn_interrupts below covers the
        INTERRUPTED transition.
        """
        from datetime import date
        from uuid import uuid4

        from synthorg.core.agent import AgentIdentity, ModelConfig
        from synthorg.core.enums import (
            Complexity,
            Priority,
            SeniorityLevel,
            TaskType,
        )
        from synthorg.core.task import Task

        identity = AgentIdentity(
            id=uuid4(),
            name="Test Agent",
            role="Developer",
            department="Engineering",
            level=SeniorityLevel.MID,
            model=ModelConfig(
                provider="test-provider",
                model_id="test-model-001",
            ),
            hiring_date=date(2026, 1, 1),
        )

        task = Task(
            id="task-shutdown-001",
            title="Task for shutdown test",
            description="This task will complete before shutdown check.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="test",
            estimated_complexity=Complexity.SIMPLE,
            budget_limit=10.0,
            assigned_to=str(identity.id),
            status=TaskStatus.ASSIGNED,
        )

        strategy = CooperativeTimeoutStrategy(grace_seconds=5.0)
        manager = ShutdownManager(strategy=strategy)

        provider = _ShutdownTriggeringProvider(strategy)

        engine = AgentEngine(
            provider=provider,
            shutdown_checker=manager.is_shutting_down,
        )

        result = await engine.run(
            identity=identity,
            task=task,
        )

        # Loop completed normally (STOP with no tool calls)
        assert result.is_success is True
        # But the shutdown signal was set
        assert manager.is_shutting_down() is True

    async def test_shutdown_during_multi_turn_interrupts(
        self,
    ) -> None:
        """Multi-turn execution interrupted by shutdown → INTERRUPTED."""
        from datetime import date
        from uuid import uuid4

        from synthorg.core.agent import AgentIdentity, ModelConfig
        from synthorg.core.enums import (
            Complexity,
            Priority,
            SeniorityLevel,
            TaskType,
        )
        from synthorg.core.task import Task

        identity = AgentIdentity(
            id=uuid4(),
            name="Test Agent",
            role="Developer",
            department="Engineering",
            level=SeniorityLevel.MID,
            model=ModelConfig(
                provider="test-provider",
                model_id="test-model-001",
            ),
            hiring_date=date(2026, 1, 1),
        )

        task = Task(
            id="task-shutdown-002",
            title="Multi-turn shutdown test",
            description="This task will be interrupted mid-execution.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="test",
            estimated_complexity=Complexity.SIMPLE,
            budget_limit=10.0,
            assigned_to=str(identity.id),
            status=TaskStatus.ASSIGNED,
        )

        check_count = 0

        def shutdown_checker() -> bool:
            nonlocal check_count
            check_count += 1
            # Let first two checks pass (top of loop + before tools
            # on turn 1), then shutdown on third check (top of loop
            # on turn 2)
            return check_count > 2

        # Provider returns tool-use on first call so the loop iterates
        from synthorg.providers.models import ToolCall

        responses = [
            CompletionResponse(
                content=None,
                tool_calls=(ToolCall(id="tc-1", name="echo", arguments={}),),
                finish_reason=FinishReason.TOOL_USE,
                usage=TokenUsage(
                    input_tokens=50,
                    output_tokens=25,
                    cost_usd=0.005,
                ),
                model="test-model-001",
            ),
        ]

        class _MultiTurnProvider:
            def __init__(self) -> None:
                self._idx = 0

            async def complete(
                self,
                messages: Any,
                model: Any,
                **kw: Any,
            ) -> CompletionResponse:
                if self._idx < len(responses):
                    resp = responses[self._idx]
                    self._idx += 1
                    return resp
                msg = "No more responses"
                raise IndexError(msg)

            async def stream(self, *a: Any, **kw: Any) -> Any:
                raise NotImplementedError

            async def get_model_capabilities(self, model: str) -> Any:
                from synthorg.providers.capabilities import ModelCapabilities

                return ModelCapabilities(
                    model_id=model,
                    provider="test-provider",
                    supports_tools=True,
                    supports_streaming=False,
                    max_context_tokens=8192,
                    max_output_tokens=4096,
                    cost_per_1k_input=0.01,
                    cost_per_1k_output=0.03,
                )

        from synthorg.core.enums import ToolCategory
        from synthorg.tools.base import BaseTool, ToolExecutionResult
        from synthorg.tools.registry import ToolRegistry

        class _EchoTool(BaseTool):
            def __init__(self) -> None:
                super().__init__(
                    name="echo",
                    description="Echo tool",
                    category=ToolCategory.CODE_EXECUTION,
                )

            async def execute(
                self,
                *,
                arguments: dict[str, Any],
            ) -> ToolExecutionResult:
                return ToolExecutionResult(content="echoed", is_error=False)

        registry = ToolRegistry([_EchoTool()])

        engine = AgentEngine(
            provider=_MultiTurnProvider(),
            tool_registry=registry,
            shutdown_checker=shutdown_checker,
        )

        result = await engine.run(
            identity=identity,
            task=task,
        )

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status == TaskStatus.INTERRUPTED
        assert result.execution_result.termination_reason.value == "shutdown"
