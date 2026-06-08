"""Tests for procedural memory integration in the agent engine."""

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.execution.turn import TurnRecord
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
)
from tests._shared import as_uuid

_AGENT_UUID = as_uuid("procedural-agent")


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
        id=as_uuid("task-proc-001"),
        title="Implement feature X",
        description="Build the X feature.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


def _make_error_execution_result(identity: AgentIdentity) -> ExecutionResult:
    task = _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    turns = (
        TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.001,
            tool_calls_made=("code_search",),
            finish_reason=FinishReason.STOP,
        ),
    )
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.ERROR,
        turns=turns,
        error_message="Provider timeout",
    )


def _make_completed_execution_result(
    identity: AgentIdentity,
) -> ExecutionResult:
    task = _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )


_VALID_PROPOSAL_CONTENT = json.dumps(
    {
        "discovery": "Break tasks into subtasks when facing timeouts.",
        "condition": "Task fails due to provider timeout.",
        "action": "Decompose the task before retrying.",
        "rationale": "Smaller tasks reduce context pressure.",
        "execution_steps": ["Analyse failure", "Split into subtasks"],
        "confidence": 0.85,
        "tags": ["timeout"],
    },
)


def _make_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content=_VALID_PROPOSAL_CONTENT,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
            model="test-small-001",
        ),
    )
    return provider


@pytest.mark.unit
class TestAgentEngineProcedural:
    async def test_procedural_memory_generated_after_error(self) -> None:
        """Procedural memory pipeline runs after ERROR recovery."""
        identity = _make_identity()
        provider = _make_provider()
        memory_backend = AsyncMock(spec=MemoryBackend)
        memory_backend.store = AsyncMock(return_value="mem-001")
        config = ProceduralMemoryConfig(model="test-small-001")

        engine = AgentEngine(
            provider=provider,
            recovery_strategy=FailAndReassignStrategy(),
            procedural_memory_config=config,
            memory_backend=memory_backend,
        )

        error_result = _make_error_execution_result(identity)
        agent_id = str(identity.id)
        task_id = "task-proc-001"

        # Test _post_execution_pipeline directly to isolate
        # procedural memory integration from run() validation.
        await engine._post_execution_pipeline(
            error_result,
            identity,
            agent_id,
            task_id,
        )

        # Memory backend should be called (proposer -> store)
        memory_backend.store.assert_awaited_once()

    async def test_no_procedural_memory_for_non_error(self) -> None:
        """Procedural memory skipped for non-ERROR terminations."""
        identity = _make_identity()
        provider = _make_provider()
        memory_backend = AsyncMock(spec=MemoryBackend)
        config = ProceduralMemoryConfig(model="test-small-001")

        engine = AgentEngine(
            provider=provider,
            recovery_strategy=FailAndReassignStrategy(),
            procedural_memory_config=config,
            memory_backend=memory_backend,
        )

        completed_result = _make_completed_execution_result(identity)

        with patch.object(
            engine,
            "_execute",
            return_value=completed_result,
        ):
            await engine.run(identity=identity, task=_make_task())

        # Memory backend should NOT be called for successful runs
        memory_backend.store.assert_not_awaited()

    async def test_procedural_memory_failure_does_not_block(self) -> None:
        """Failure in procedural memory pipeline does not block result."""
        identity = _make_identity()
        provider = _make_provider()
        # Make the proposer call raise an error
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM error"))
        memory_backend = AsyncMock(spec=MemoryBackend)
        config = ProceduralMemoryConfig(model="test-small-001")

        engine = AgentEngine(
            provider=provider,
            recovery_strategy=FailAndReassignStrategy(),
            procedural_memory_config=config,
            memory_backend=memory_backend,
        )

        error_result = _make_error_execution_result(identity)

        with patch.object(
            engine,
            "_execute",
            return_value=error_result,
        ):
            # Should not raise despite the proposer error
            result = await engine.run(
                identity=identity,
                task=_make_task(),
            )

        assert result is not None

    async def test_no_procedural_memory_without_config(self) -> None:
        """No procedural memory when config is None."""
        identity = _make_identity()
        provider = _make_provider()
        memory_backend = AsyncMock(spec=MemoryBackend)

        engine = AgentEngine(
            provider=provider,
            recovery_strategy=FailAndReassignStrategy(),
            procedural_memory_config=None,
            memory_backend=memory_backend,
        )

        error_result = _make_error_execution_result(identity)

        with patch.object(
            engine,
            "_execute",
            return_value=error_result,
        ):
            await engine.run(identity=identity, task=_make_task())

        memory_backend.store.assert_not_awaited()

    async def test_no_procedural_memory_without_backend(self) -> None:
        """No procedural memory when memory_backend is None."""
        identity = _make_identity()
        provider = _make_provider()
        config = ProceduralMemoryConfig(model="test-small-001")

        engine = AgentEngine(
            provider=provider,
            recovery_strategy=FailAndReassignStrategy(),
            procedural_memory_config=config,
            memory_backend=None,
        )

        error_result = _make_error_execution_result(identity)

        with patch.object(
            engine,
            "_execute",
            return_value=error_result,
        ):
            # Should complete without error
            result = await engine.run(
                identity=identity,
                task=_make_task(),
            )

        assert result is not None

    async def test_no_procedural_memory_when_disabled(self) -> None:
        """No procedural memory when config.enabled is False.

        Verifies the proposer is never constructed (not just that
        store is not called).
        """
        identity = _make_identity()
        provider = _make_provider()
        memory_backend = AsyncMock(spec=MemoryBackend)
        config = ProceduralMemoryConfig(
            model="test-small-001",
            enabled=False,
        )

        engine = AgentEngine(
            provider=provider,
            recovery_strategy=FailAndReassignStrategy(),
            procedural_memory_config=config,
            memory_backend=memory_backend,
        )

        # Proposer should not be constructed when disabled
        assert engine._procedural_proposer is None

        error_result = _make_error_execution_result(identity)

        with patch.object(
            engine,
            "_execute",
            return_value=error_result,
        ):
            await engine.run(identity=identity, task=_make_task())

        memory_backend.store.assert_not_awaited()
