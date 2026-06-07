"""Tests for distillation capture integration in the agent engine."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.execution.turn import TurnRecord
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.enums import FinishReason
from synthorg.providers.protocol import CompletionProvider
from tests._shared import as_uuid

_AGENT_UUID = as_uuid("distillation-agent")
_TASK_UUID = as_uuid("task-dist-001")


def _make_identity() -> AgentIdentity:
    return AgentIdentity(
        id=_AGENT_UUID,
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _make_task() -> Task:
    return Task(
        id=_TASK_UUID,
        title="Implement feature Y",
        description="Build the Y feature.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


def _make_completed_result(identity: AgentIdentity) -> ExecutionResult:
    task = _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    turns = (
        TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.001,
            tool_calls_made=("search_memory", "code_search"),
            finish_reason=FinishReason.STOP,
        ),
        TurnRecord(
            turn_number=2,
            input_tokens=80,
            output_tokens=40,
            cost=0.001,
            tool_calls_made=(),
            finish_reason=FinishReason.STOP,
        ),
    )
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
    )


def _make_error_result(identity: AgentIdentity) -> ExecutionResult:
    task = _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.ERROR,
        turns=(),
        error_message="provider timeout",
    )


@pytest.mark.unit
class TestAgentEngineDistillationCapture:
    async def test_distillation_captured_when_enabled(self) -> None:
        """Distillation is stored on successful task completion when enabled."""
        identity = _make_identity()
        provider = AsyncMock(spec=CompletionProvider)
        memory_backend = AsyncMock(spec=MemoryBackend)
        memory_backend.store = AsyncMock(return_value="dist-1")

        engine = AgentEngine(
            provider=provider,
            memory_backend=memory_backend,
            distillation_capture_enabled=True,
        )

        result = _make_completed_result(identity)
        await engine._post_execution_pipeline(
            result,
            identity,
            str(identity.id),
            str(_TASK_UUID),
        )

        memory_backend.store.assert_awaited_once()
        store_call = memory_backend.store.call_args
        store_request = store_call.args[1]
        assert "distillation" in store_request.metadata.tags
        assert store_request.metadata.source == "distillation"
        assert "Task completed" in store_request.content

    async def test_distillation_captured_on_error_termination(self) -> None:
        """Distillation captures failed runs too -- trajectory context matters."""
        identity = _make_identity()
        provider = AsyncMock(spec=CompletionProvider)
        memory_backend = AsyncMock(spec=MemoryBackend)
        memory_backend.store = AsyncMock(return_value="dist-err")

        engine = AgentEngine(
            provider=provider,
            memory_backend=memory_backend,
            distillation_capture_enabled=True,
        )

        error_result = _make_error_result(identity)
        await engine._post_execution_pipeline(
            error_result,
            identity,
            str(identity.id),
            str(_TASK_UUID),
        )

        memory_backend.store.assert_awaited_once()
        store_request = memory_backend.store.call_args.args[1]
        assert "Task failed" in store_request.content
        assert "provider timeout" in store_request.content

    async def test_distillation_disabled_by_default(self) -> None:
        """Distillation capture is opt-in -- default is disabled."""
        identity = _make_identity()
        provider = AsyncMock(spec=CompletionProvider)
        memory_backend = AsyncMock(spec=MemoryBackend)

        engine = AgentEngine(
            provider=provider,
            memory_backend=memory_backend,
            # distillation_capture_enabled omitted -- default False
        )

        result = _make_completed_result(identity)
        await engine._post_execution_pipeline(
            result,
            identity,
            str(identity.id),
            str(_TASK_UUID),
        )

        memory_backend.store.assert_not_awaited()

    async def test_distillation_without_backend_is_noop(self) -> None:
        """No backend + flag set -> quietly skip, do not crash."""
        identity = _make_identity()
        provider = AsyncMock(spec=CompletionProvider)

        engine = AgentEngine(
            provider=provider,
            memory_backend=None,
            distillation_capture_enabled=True,
        )

        result = _make_completed_result(identity)
        # Should complete without raising.
        await engine._post_execution_pipeline(
            result,
            identity,
            str(identity.id),
            str(_TASK_UUID),
        )
