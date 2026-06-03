"""Tests for distillation request capture."""

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
import structlog.testing
from pydantic import ValidationError
from typeguard import suppress_type_checks

from synthorg.core.enums import MemoryCategory
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.memory.consolidation.distillation import (
    DistillationRequest,
    MemoryToolName,
    build_outcome,
    build_trajectory_summary,
    capture_distillation,
    extract_memory_tool_invocations,
)
from synthorg.memory.errors import MemoryStoreError
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability.events.consolidation import DISTILLATION_CAPTURE_FAILED
from synthorg.providers.enums import FinishReason


def _make_turn(
    *,
    turn_number: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 50,
    tool_calls_made: tuple[str, ...] = (),
) -> TurnRecord:
    """Helper to build a TurnRecord."""
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.001,
        tool_calls_made=tool_calls_made,
        finish_reason=FinishReason.STOP,
    )


def _make_execution_result(
    *,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
    error_message: str | None = None,
    turns: tuple[TurnRecord, ...] = (),
) -> ExecutionResult:
    """Build a lightweight execution result duck-type for testing.

    Uses ``SimpleNamespace`` because ``ExecutionResult`` requires a
    real ``AgentContext`` and we only need ``.turns``,
    ``.termination_reason``, and ``.error_message`` for distillation.
    """
    namespace = SimpleNamespace(
        termination_reason=termination_reason,
        turns=turns,
        error_message=error_message,
    )
    return cast("ExecutionResult", namespace)


# ── DistillationRequest model ──────────────────────────────────


@pytest.mark.unit
class TestDistillationRequest:
    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        req = DistillationRequest(
            agent_id="agent-1",
            task_id="task-1",
            trajectory_summary="3 turns, 450 tokens",
            outcome="Task completed successfully.",
            memory_tool_invocations=(MemoryToolName.SEARCH_MEMORY,),
            created_at=now,
        )
        assert req.agent_id == "agent-1"
        assert req.task_id == "task-1"
        assert req.memory_tool_invocations == (MemoryToolName.SEARCH_MEMORY,)

    def test_frozen_rejects_mutation(self) -> None:
        now = datetime.now(UTC)
        req = DistillationRequest(
            agent_id="agent-1",
            task_id="task-1",
            trajectory_summary="summary",
            outcome="outcome",
            created_at=now,
        )
        with pytest.raises(ValidationError):
            req.agent_id = "other"  # type: ignore[misc]

    def test_default_memory_tool_invocations(self) -> None:
        now = datetime.now(UTC)
        req = DistillationRequest(
            agent_id="agent-1",
            task_id="task-1",
            trajectory_summary="summary",
            outcome="outcome",
            created_at=now,
        )
        assert req.memory_tool_invocations == ()

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DistillationRequest(
                agent_id="  ",
                task_id="task-1",
                trajectory_summary="summary",
                outcome="outcome",
                created_at=datetime.now(UTC),
            )


# ── build_trajectory_summary ─────────────────────────────────────


@pytest.mark.unit
class TestBuildTrajectorySummary:
    def test_empty_turns(self) -> None:
        result = build_trajectory_summary(())
        assert result == "No turns recorded."

    def test_single_turn_no_tools(self) -> None:
        turns = (_make_turn(),)
        result = build_trajectory_summary(turns)
        assert "1 turn" in result
        assert "150 tokens" in result

    def test_multiple_turns_with_tools(self) -> None:
        turns = (
            _make_turn(
                turn_number=1,
                tool_calls_made=("search_memory", "code_execute"),
            ),
            _make_turn(
                turn_number=2,
                tool_calls_made=("search_memory",),
            ),
        )
        result = build_trajectory_summary(turns)
        assert "2 turns" in result
        assert "300 tokens" in result
        assert "code_execute" in result
        assert "search_memory" in result
        assert "3 tool calls total" in result


# ── build_outcome ─────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildOutcome:
    def test_completed(self) -> None:
        result = build_outcome(TerminationReason.COMPLETED, None)
        assert result == "Task completed successfully."

    def test_error_with_message(self) -> None:
        result = build_outcome(TerminationReason.ERROR, "timeout exceeded")
        assert "Task failed" in result
        assert "timeout exceeded" in result

    def test_other_reason(self) -> None:
        result = build_outcome(TerminationReason.MAX_TURNS, None)
        assert "max_turns" in result

    def test_budget_exhausted(self) -> None:
        result = build_outcome(TerminationReason.BUDGET_EXHAUSTED, None)
        assert "budget_exhausted" in result

    def test_error_without_message_falls_through(self) -> None:
        """ERROR termination without error_message hits the fallback branch."""
        result = build_outcome(TerminationReason.ERROR, None)
        assert "terminated" in result.lower()
        assert "error" in result.lower()


# ── extract_memory_tool_invocations ──────────────────────────────────────────


@pytest.mark.unit
class TestExtractMemoryToolInvocations:
    def test_no_tool_calls(self) -> None:
        turns = (_make_turn(),)
        result = extract_memory_tool_invocations(turns)
        assert result == ()

    def test_memory_tools_found(self) -> None:
        turns = (
            _make_turn(tool_calls_made=("search_memory", "code_execute")),
            _make_turn(tool_calls_made=("recall_memory",)),
        )
        result = extract_memory_tool_invocations(turns)
        assert "search_memory" in result
        assert "recall_memory" in result
        assert "code_execute" not in result

    def test_non_memory_tools_ignored(self) -> None:
        turns = (_make_turn(tool_calls_made=("code_execute", "web_search")),)
        result = extract_memory_tool_invocations(turns)
        assert result == ()

    def test_empty_turns(self) -> None:
        result = extract_memory_tool_invocations(())
        assert result == ()


# ── capture_distillation ────────────────────────────────────────


@pytest.mark.unit
class TestCaptureDistillation:
    @pytest.fixture(autouse=True)
    def _suppress_typeguard(self) -> Iterator[None]:
        """Suppress typeguard: tests pass a ``SimpleNamespace`` ExecutionResult.

        ``capture_distillation`` is exercised with a ``SimpleNamespace``
        stand-in for ``ExecutionResult`` to drive capture paths; these verify
        capture behaviour, not result type conformance.
        """
        with suppress_type_checks():
            yield

    async def test_successful_capture(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="dist-1")

        exec_result = _make_execution_result(
            turns=(
                _make_turn(tool_calls_made=("search_memory",)),
                _make_turn(),
            ),
        )

        result = await capture_distillation(
            exec_result,
            agent_id="agent-1",
            task_id="task-1",
            backend=backend,
        )

        assert result is not None
        assert result.agent_id == "agent-1"
        assert result.task_id == "task-1"
        assert "search_memory" in result.memory_tool_invocations
        backend.store.assert_called_once()

    async def test_backend_error_returns_none(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(
            side_effect=MemoryStoreError("store failed"),
        )

        exec_result = _make_execution_result()

        with structlog.testing.capture_logs() as logs:
            result = await capture_distillation(
                exec_result,
                agent_id="agent-1",
                task_id="task-1",
                backend=backend,
            )

        assert result is None
        failed_events = [
            e for e in logs if e.get("event") == DISTILLATION_CAPTURE_FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0]["agent_id"] == "agent-1"
        assert failed_events[0]["task_id"] == "task-1"

    async def test_memory_error_propagates(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(side_effect=MemoryError("oom"))

        exec_result = _make_execution_result()

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(MemoryError),
        ):
            await capture_distillation(
                exec_result,
                agent_id="agent-1",
                task_id="task-1",
                backend=backend,
            )

        failed_events = [
            e for e in logs if e.get("event") == DISTILLATION_CAPTURE_FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0]["error_type"] == "system"

    async def test_error_termination_captured(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="dist-2")

        exec_result = _make_execution_result(
            termination_reason=TerminationReason.ERROR,
            error_message="something broke",
        )

        result = await capture_distillation(
            exec_result,
            agent_id="agent-1",
            task_id="task-1",
            backend=backend,
        )

        assert result is not None
        assert "Task failed" in result.outcome
        assert "something broke" in result.outcome

    async def test_recursion_error_propagates(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(side_effect=RecursionError("stack overflow"))

        exec_result = _make_execution_result()

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(RecursionError),
        ):
            await capture_distillation(
                exec_result,
                agent_id="agent-1",
                task_id="task-1",
                backend=backend,
            )

        failed_events = [
            e for e in logs if e.get("event") == DISTILLATION_CAPTURE_FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0]["error_type"] == "system"

    async def test_store_request_shape(self) -> None:
        """Verify capture_distillation stores the expected EPISODIC tag shape."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="dist-3")

        exec_result = _make_execution_result(
            turns=(_make_turn(tool_calls_made=("search_memory",)),),
        )

        await capture_distillation(
            exec_result,
            agent_id="agent-7",
            task_id="task-7",
            backend=backend,
        )

        backend.store.assert_called_once()
        store_call = backend.store.call_args
        assert store_call.args[0] == "agent-7"
        store_request = store_call.args[1]
        assert store_request.category == MemoryCategory.EPISODIC
        assert store_request.metadata.source == "distillation"
        assert "distillation" in store_request.metadata.tags
        assert "Task ID: task-7" in store_request.content
        assert "Trajectory:" in store_request.content
        assert "Memory tool invocations: search_memory" in store_request.content
