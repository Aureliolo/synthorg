"""Tests for FailureCaptureStrategy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.task_enums import TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.recovery import RecoveryResult
from synthorg.memory.procedural.capture.failure_capture import FailureCaptureStrategy
from synthorg.memory.procedural.models import (
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)


@pytest.mark.unit
class TestFailureCaptureStrategy:
    """Tests for FailureCaptureStrategy."""

    @pytest.fixture
    def proposer(self) -> AsyncMock:
        """Mock proposer."""
        return AsyncMock()

    @pytest.fixture
    def memory_backend(self) -> AsyncMock:
        """Mock memory backend."""
        return AsyncMock()

    @pytest.fixture
    def config(self) -> ProceduralMemoryConfig:
        """Config for the proposer."""
        return ProceduralMemoryConfig(
            enabled=True,
            model="test-model",
            temperature=0.3,
            max_tokens=1500,
            min_confidence=0.5,
        )

    @pytest.fixture
    def strategy(
        self,
        proposer: AsyncMock,
        config: ProceduralMemoryConfig,
    ) -> FailureCaptureStrategy:
        """Create a FailureCaptureStrategy."""
        return FailureCaptureStrategy(proposer=proposer, config=config)

    @pytest.fixture
    def execution_result(self) -> MagicMock:
        """Mock execution result."""
        result = MagicMock(spec=ExecutionResult)
        result.turns = []
        result.termination_reason = TerminationReason.MAX_TURNS
        return result

    @pytest.fixture
    def recovery_result(self) -> MagicMock:
        """Mock recovery result."""
        result = MagicMock(spec=RecoveryResult)
        result.error_message = "Test error message"
        result.strategy_type = "retry"
        result.task_execution = MagicMock()
        result.task_execution.task = MagicMock(
            id="task-1",
            title="Test Task",
            description="Test task description",
            type=TaskType.DEVELOPMENT,
            max_retries=3,
        )
        result.task_execution.retry_count = 1
        result.context_snapshot = MagicMock()
        result.context_snapshot.turn_count = 5
        result.can_reassign = False
        return result

    async def test_name_property(self, strategy: FailureCaptureStrategy) -> None:
        """Test that name property returns expected value."""
        assert strategy.name == "failure"

    async def test_capture_returns_none_when_no_recovery(
        self,
        strategy: FailureCaptureStrategy,
        execution_result: MagicMock,
        memory_backend: AsyncMock,
    ) -> None:
        """Test that capture returns None when recovery_result is None."""
        result = await strategy.capture(
            execution_result=execution_result,
            recovery_result=None,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            memory_backend=memory_backend,
        )
        assert result is None

    async def test_capture_delegates_to_proposer_on_recovery(
        self,
        strategy: FailureCaptureStrategy,
        proposer: AsyncMock,
        execution_result: MagicMock,
        recovery_result: MagicMock,
        memory_backend: AsyncMock,
    ) -> None:
        """Test that capture delegates to the existing pipeline when recovery exists."""
        proposal = ProceduralMemoryProposal(
            discovery="Test discovery",
            condition="Test condition",
            action="Test action",
            rationale="Test rationale",
            confidence=0.8,
            tags=("test",),
        )
        memory_id = NotBlankStr("mem-123")

        # Mock the propose method
        proposer.propose = AsyncMock(return_value=proposal)
        memory_backend.store = AsyncMock(return_value=memory_id)

        result = await strategy.capture(
            execution_result=execution_result,
            recovery_result=recovery_result,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            memory_backend=memory_backend,
        )

        # Should have called the proposer
        proposer.propose.assert_called_once()
        # Should have stored the memory
        memory_backend.store.assert_called_once()
        # Should return the memory ID
        assert result == memory_id

    async def test_capture_returns_none_when_proposer_returns_none(
        self,
        strategy: FailureCaptureStrategy,
        proposer: AsyncMock,
        execution_result: MagicMock,
        recovery_result: MagicMock,
        memory_backend: AsyncMock,
    ) -> None:
        """Test that capture returns None when proposer returns None."""
        proposer.propose = AsyncMock(return_value=None)

        result = await strategy.capture(
            execution_result=execution_result,
            recovery_result=recovery_result,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            memory_backend=memory_backend,
        )

        assert result is None
        proposer.propose.assert_called_once()
        memory_backend.store.assert_not_called()
