"""Tests for the per-task cancellation safe-boundary check."""

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_cancellation import check_task_cancelled
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason


@pytest.mark.unit
class TestCheckTaskCancelled:
    """check_task_cancelled halts a run whose task was cancelled externally."""

    async def test_none_checker_continues(
        self, sample_agent_context: AgentContext
    ) -> None:
        assert await check_task_cancelled(sample_agent_context, None, []) is None

    async def test_not_cancelled_continues(
        self, sample_agent_context: AgentContext
    ) -> None:
        async def _not_cancelled() -> bool:
            return False

        assert (
            await check_task_cancelled(sample_agent_context, _not_cancelled, []) is None
        )

    async def test_cancelled_returns_cancelled_result(
        self, sample_agent_context: AgentContext
    ) -> None:
        async def _cancelled() -> bool:
            return True

        result = await check_task_cancelled(sample_agent_context, _cancelled, [])
        assert isinstance(result, ExecutionResult)
        assert result.termination_reason is TerminationReason.CANCELLED
        assert result.error_message is None

    async def test_checker_failure_is_best_effort(
        self, sample_agent_context: AgentContext
    ) -> None:
        async def _boom() -> bool:
            msg = "db blip"
            raise RuntimeError(msg)

        assert await check_task_cancelled(sample_agent_context, _boom, []) is None
