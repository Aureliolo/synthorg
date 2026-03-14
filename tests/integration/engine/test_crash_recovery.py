"""Integration test: crash recovery full flow.

Engine.run() with failing provider -> task FAILED -> can_reassign checks.
"""

from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
)
from synthorg.core.enums import Priority, SeniorityLevel, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.task_execution import TaskExecution

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from synthorg.providers.capabilities import ModelCapabilities
    from synthorg.providers.models import (
        ChatMessage,
        CompletionConfig,
        CompletionResponse,
        StreamChunk,
        ToolDefinition,
    )

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


class _FailingProvider:
    """Mock provider that always raises on complete()."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("Provider crashed")

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        raise self._error

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        msg = "stream not supported"
        raise NotImplementedError(msg)

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
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


def _make_identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Recovery Agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
    )


def _make_task(
    identity: AgentIdentity,
    *,
    max_retries: int = 1,
) -> Task:
    return Task(
        id="task-recovery",
        title="Crash recovery test",
        description="Test the crash recovery flow.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-001",
        created_by="manager",
        assigned_to=str(identity.id),
        status=TaskStatus.ASSIGNED,
        max_retries=max_retries,
    )


class TestCrashRecoveryFlow:
    """Full flow: engine.run() with failing provider -> FAILED."""

    async def test_first_failure_can_reassign(self) -> None:
        """First failure with max_retries=1 -> FAILED, can_reassign=True."""
        identity = _make_identity()
        task = _make_task(identity, max_retries=1)
        provider = _FailingProvider()

        engine = AgentEngine(provider=provider)
        result = await engine.run(identity=identity, task=task)

        assert result.termination_reason == TerminationReason.ERROR
        assert result.is_success is False

        te = result.execution_result.context.task_execution
        assert te is not None
        assert te.status is TaskStatus.FAILED

        # retry_count=0 < max_retries=1 means reassignment is possible
        assert te.retry_count < task.max_retries

    async def test_second_failure_cannot_reassign(self) -> None:
        """Second failure (retry_count=1, max_retries=1) -> cannot reassign."""
        identity = _make_identity()
        task = _make_task(identity, max_retries=1)
        provider = _FailingProvider()

        # First run
        engine = AgentEngine(provider=provider)
        first_result = await engine.run(identity=identity, task=task)

        first_te = first_result.execution_result.context.task_execution
        assert first_te is not None
        assert first_te.status is TaskStatus.FAILED

        # Simulate reassignment: create new task in ASSIGNED status with
        # retry_count from the first execution + 1
        reassigned_task = task.model_copy(
            update={"status": TaskStatus.ASSIGNED},
        )
        # Create new execution with incremented retry_count
        new_exe = TaskExecution.from_task(
            reassigned_task,
            retry_count=first_te.retry_count + 1,
        )
        assert new_exe.retry_count == 1

        # The reassigned execution should indicate no more retries
        assert new_exe.retry_count >= task.max_retries
