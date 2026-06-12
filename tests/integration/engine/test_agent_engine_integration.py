"""Integration test: AgentEngine -> ReactLoop -> tool calls -> result.

Demonstrates the full execution pipeline with a real ToolRegistry,
real ReactLoop, and a mock provider that returns tool calls.
"""

from collections.abc import AsyncIterator, Mapping
from datetime import date
from typing import override
from uuid import uuid4

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.registry import ToolRegistry
from tests._shared import as_uuid

pytestmark = pytest.mark.integration


class UppercaseTool(BaseTool):
    """Test tool that uppercases input text."""

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Uppercase the 'text' argument."""
        text = str(arguments.get("text", ""))
        return ToolExecutionResult(content=text.upper())


class _ToolCallingProvider:
    """Mock provider that issues a tool call on the first turn.

    Turn 1: Returns a tool call for the 'uppercase' tool.
    Turn 2: Returns a final text response incorporating the tool result.
    """

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Return tool call on turn 1, text response on turn 2."""
        self._call_count += 1

        if self._call_count == 1:
            return CompletionResponse(
                content="",
                finish_reason=FinishReason.TOOL_USE,
                usage=TokenUsage(
                    input_tokens=50,
                    output_tokens=20,
                    cost=0.005,
                ),
                model="test-model-001",
                tool_calls=(
                    ToolCall(
                        id="call-001",
                        name="uppercase",
                        arguments={"text": "hello world"},
                    ),
                ),
            )

        return CompletionResponse(
            content="The uppercased text is: HELLO WORLD",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=80,
                output_tokens=30,
                cost=0.008,
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
    ) -> AsyncIterator[StreamChunk]:
        """Not implemented for this test."""
        msg = "stream not supported"
        raise NotImplementedError(msg)

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Return minimal capabilities."""
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

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Return capabilities for each requested model."""
        return {m: await self.get_model_capabilities(m) for m in models}


class TestAgentEngineToolCallIntegration:
    """Full pipeline: AgentEngine -> ReactLoop -> tool execution -> result."""

    async def test_full_tool_call_loop(self) -> None:
        """Agent makes a tool call, gets result, produces final answer."""
        identity = AgentIdentity(
            id=uuid4(),
            name="Test Agent",
            role="Developer",
            department="Engineering",
            level=SeniorityLevel.MID,
            hiring_date=date(2026, 1, 15),
            personality=PersonalityConfig(
                traits=("analytical",),
            ),
            model=ModelConfig(
                provider="test-provider",
                model_id="test-model-001",
            ),
        )
        task = Task(
            id=as_uuid("task-integration"),
            title="Uppercase a string",
            description="Use the uppercase tool to convert text.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="manager",
            assigned_to=str(identity.id),
            status=TaskStatus.ASSIGNED,
        )

        tool = UppercaseTool(
            name="uppercase",
            description="Converts text to uppercase.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to uppercase"},
                },
                "required": ["text"],
            },
            category=ToolCategory.CODE_EXECUTION,
        )
        registry = ToolRegistry([tool])
        provider = _ToolCallingProvider()

        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
        )

        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=5,
        )

        # Verify successful completion
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_turns == 2  # tool call turn + final answer turn

        # Verify cost was accumulated across turns
        assert result.total_cost > 0
        assert result.duration_seconds > 0

        # Verify the tool was actually called (result in conversation).
        # Tool result content is wrapped in a ``<tool-result>`` fence
        # before entering the conversation so the model cannot
        # mistake tool output for instructions.
        conversation = result.execution_result.context.conversation
        tool_results = [m for m in conversation if m.tool_result is not None]
        assert len(tool_results) == 1
        assert tool_results[0].tool_result is not None
        wrapped = tool_results[0].tool_result.content
        assert wrapped.startswith("<tool-result>\n")
        assert wrapped.endswith("\n</tool-result>")
        assert "HELLO WORLD" in wrapped

        # Verify task parks at IN_REVIEW: ASSIGNED -> IP -> IR
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW


class TestAgentEngineFullLifecycle:
    """Full task lifecycle: ASSIGNED -> IN_PROGRESS -> IN_REVIEW (review gate)."""

    async def test_full_lifecycle_assigned_to_in_review(self) -> None:
        """Verify lifecycle parks at IN_REVIEW (review gate)."""
        identity = AgentIdentity(
            id=uuid4(),
            name="Lifecycle Agent",
            role="Developer",
            department="Engineering",
            level=SeniorityLevel.MID,
            hiring_date=date(2026, 1, 15),
            personality=PersonalityConfig(traits=("analytical",)),
            model=ModelConfig(
                provider="test-provider",
                model_id="test-model-001",
            ),
        )
        task = Task(
            id=as_uuid("task-lifecycle"),
            title="Full lifecycle test",
            description="Test the complete task lifecycle.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-001",
            created_by="manager",
            assigned_to=str(identity.id),
            status=TaskStatus.ASSIGNED,
        )

        tool = UppercaseTool(
            name="uppercase",
            description="Converts text to uppercase.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to uppercase",
                    },
                },
                "required": ["text"],
            },
            category=ToolCategory.CODE_EXECUTION,
        )
        registry = ToolRegistry([tool])
        provider = _ToolCallingProvider()

        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
        )

        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=5,
        )

        # Verify successful completion
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED

        # Verify transition log: ASSIGNED->IP, IP->IR (review gate)
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW
        assert len(task_execution.transition_log) == 2
        assert task_execution.transition_log[0].to_status == TaskStatus.IN_PROGRESS
        assert task_execution.transition_log[1].to_status == TaskStatus.IN_REVIEW

        # completed_at is NOT set -- task awaits human review
        assert task_execution.completed_at is None

        # Verify completion_summary is non-empty
        assert result.completion_summary is not None
        assert len(result.completion_summary) > 0

        # Verify TaskCompletionMetrics computable
        from synthorg.engine.metrics import TaskCompletionMetrics

        metrics = TaskCompletionMetrics.from_run_result(result)
        assert metrics.turns_per_task > 0
        assert metrics.tokens_per_task > 0
        assert metrics.cost_per_task > 0
        assert metrics.duration_seconds > 0


class TestPermissionDeniedToolCall:
    """Integration: tool call denied by permission checker returns error result."""

    async def test_denied_tool_returns_permission_error(self) -> None:
        """Agent with SANDBOXED access gets denied for a DEPLOYMENT tool."""
        identity = AgentIdentity(
            id=uuid4(),
            name="Sandboxed Agent",
            role="Intern",
            department="Engineering",
            level=SeniorityLevel.JUNIOR,
            hiring_date=date(2026, 1, 15),
            personality=PersonalityConfig(traits=("cautious",)),
            model=ModelConfig(
                provider="test-provider",
                model_id="test-model-001",
            ),
            tools=ToolPermissions(
                access_level=ToolAccessLevel.SANDBOXED,
            ),
        )
        task = Task(
            id=as_uuid("task-denied"),
            title="Try deploying",
            description="Attempt to use a deployment tool.",
            type=TaskType.DEVELOPMENT,
            priority=Priority.LOW,
            project="proj-001",
            created_by="manager",
            assigned_to=str(identity.id),
            status=TaskStatus.ASSIGNED,
        )

        # Tool category is DEPLOYMENT -- not allowed at SANDBOXED level
        tool = UppercaseTool(
            name="uppercase",
            description="Converts text to uppercase.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to uppercase",
                    },
                },
                "required": ["text"],
            },
            category=ToolCategory.DEPLOYMENT,
        )
        registry = ToolRegistry([tool])
        provider = _ToolCallingProvider()

        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
        )

        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=5,
        )

        assert result.is_success is True

        # The tool call should produce a Permission denied error result
        conversation = result.execution_result.context.conversation
        tool_results = [m for m in conversation if m.tool_result is not None]
        assert len(tool_results) == 1
        assert tool_results[0].tool_result is not None
        assert tool_results[0].tool_result.is_error is True
        assert "Permission denied" in tool_results[0].tool_result.content
