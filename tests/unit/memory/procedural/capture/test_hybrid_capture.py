"""Tests for HybridCaptureStrategy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.recovery import RecoveryResult
from synthorg.memory.procedural.capture.hybrid_capture import HybridCaptureStrategy


@pytest.mark.unit
class TestHybridCaptureStrategy:
    """Tests for HybridCaptureStrategy."""

    @pytest.fixture
    def failure_strategy(self) -> MagicMock:
        """Mock failure capture strategy."""
        strategy = MagicMock()
        strategy.name = "failure"
        strategy.capture = AsyncMock()
        return strategy

    @pytest.fixture
    def success_strategy(self) -> MagicMock:
        """Mock success capture strategy."""
        strategy = MagicMock()
        strategy.name = "success"
        strategy.capture = AsyncMock()
        return strategy

    @pytest.fixture
    def hybrid_strategy(
        self,
        failure_strategy: MagicMock,
        success_strategy: MagicMock,
    ) -> HybridCaptureStrategy:
        """Create a HybridCaptureStrategy."""
        return HybridCaptureStrategy(
            failure_strategy=failure_strategy,
            success_strategy=success_strategy,
        )

    @pytest.fixture
    def memory_backend(self) -> AsyncMock:
        """Mock memory backend."""
        return AsyncMock()

    @pytest.fixture
    def execution_result_success(self) -> MagicMock:
        """Mock execution result with success termination."""
        result = MagicMock(spec=ExecutionResult)
        result.turns = []
        result.termination_reason = TerminationReason.COMPLETED
        return result

    @pytest.fixture
    def execution_result_failure(self) -> MagicMock:
        """Mock execution result with failure termination."""
        result = MagicMock(spec=ExecutionResult)
        result.turns = []
        result.termination_reason = TerminationReason.MAX_TURNS
        return result

    @pytest.fixture
    def recovery_result(self) -> MagicMock:
        """Mock recovery result."""
        return MagicMock(spec=RecoveryResult)

    async def test_name_property(self, hybrid_strategy: HybridCaptureStrategy) -> None:
        """Test that name property returns expected value."""
        assert hybrid_strategy.name == "hybrid"

    async def test_delegates_to_failure_strategy_on_recovery(  # noqa: PLR0913
        self,
        hybrid_strategy: HybridCaptureStrategy,
        failure_strategy: MagicMock,
        success_strategy: MagicMock,
        execution_result_failure: MagicMock,
        recovery_result: MagicMock,
        memory_backend: AsyncMock,
    ) -> None:
        """Test hybrid delegates to failure strategy when recovery exists."""
        memory_id = NotBlankStr("mem-failure")
        failure_strategy.capture.return_value = memory_id

        result = await hybrid_strategy.capture(
            execution_result=execution_result_failure,
            recovery_result=recovery_result,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            memory_backend=memory_backend,
        )

        failure_strategy.capture.assert_called_once()
        success_strategy.capture.assert_not_called()
        assert result == memory_id

    async def test_delegates_to_success_strategy_on_success_without_recovery(
        self,
        hybrid_strategy: HybridCaptureStrategy,
        failure_strategy: MagicMock,
        success_strategy: MagicMock,
        execution_result_success: MagicMock,
        memory_backend: AsyncMock,
    ) -> None:
        """Test hybrid delegates to success strategy on success."""
        memory_id = NotBlankStr("mem-success")
        success_strategy.capture.return_value = memory_id

        result = await hybrid_strategy.capture(
            execution_result=execution_result_success,
            recovery_result=None,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            memory_backend=memory_backend,
        )

        failure_strategy.capture.assert_not_called()
        success_strategy.capture.assert_called_once()
        assert result == memory_id

    async def test_returns_none_when_both_strategies_return_none(
        self,
        hybrid_strategy: HybridCaptureStrategy,
        failure_strategy: MagicMock,
        success_strategy: MagicMock,
        execution_result_success: MagicMock,
        memory_backend: AsyncMock,
    ) -> None:
        """Test that hybrid returns None when delegated strategy returns None."""
        success_strategy.capture.return_value = None

        result = await hybrid_strategy.capture(
            execution_result=execution_result_success,
            recovery_result=None,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            memory_backend=memory_backend,
        )

        assert result is None
