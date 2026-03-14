"""Unit tests for task_sync module — AgentEngine → TaskEngine sync functions."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError, TaskEngineError
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.engine.task_engine_models import (
    TaskErrorCode,
    TaskMutationResult,
)
from synthorg.engine.task_sync import (
    apply_post_execution_transitions,
    sync_to_task_engine,
    transition_task_if_needed,
)
from synthorg.providers.enums import FinishReason

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

pytestmark = pytest.mark.timeout(30)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_success(
    request_id: str = "test",
    version: int = 1,
) -> TaskMutationResult:
    """Build a successful TaskMutationResult for sync tests."""
    return TaskMutationResult(
        request_id=request_id,
        success=True,
        version=version,
    )


def _make_sync_failure(
    request_id: str = "test",
    error: str = "rejected",
    error_code: TaskErrorCode = "validation",
) -> TaskMutationResult:
    """Build a failed TaskMutationResult for sync tests."""
    return TaskMutationResult(
        request_id=request_id,
        success=False,
        error=error,
        error_code=error_code,
    )


def _make_mock_task_engine(
    side_effect: object | None = None,
    return_value: TaskMutationResult | None = None,
) -> MagicMock:
    """Build a mock TaskEngine with configurable submit behavior."""
    mock_te = MagicMock()
    if side_effect is not None:
        mock_te.submit = AsyncMock(side_effect=side_effect)
    else:
        mock_te.submit = AsyncMock(
            return_value=return_value or _make_sync_success(),
        )
    return mock_te


def _make_execution_result(
    ctx: AgentContext,
    reason: TerminationReason = TerminationReason.COMPLETED,
    error_message: str | None = None,
) -> ExecutionResult:
    """Build an ExecutionResult with a single dummy turn."""
    return ExecutionResult(
        context=ctx,
        termination_reason=reason,
        error_message=error_message,
        turns=(
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
                finish_reason=FinishReason.STOP,
            ),
        ),
    )


# ===================================================================
# sync_to_task_engine
# ===================================================================


@pytest.mark.unit
class TestSyncToTaskEngine:
    """Direct tests for the sync_to_task_engine function."""

    async def test_none_task_engine_is_noop(self) -> None:
        """When task_engine is None, nothing happens (no error)."""
        await sync_to_task_engine(
            None,
            target_status=TaskStatus.IN_PROGRESS,
            task_id="task-1",
            agent_id="agent-1",
            reason="test",
        )
        # No exception = success

    async def test_successful_sync(self) -> None:
        """Successful submit logs debug and returns without error."""
        mock_te = _make_mock_task_engine()

        await sync_to_task_engine(
            mock_te,
            target_status=TaskStatus.IN_PROGRESS,
            task_id="task-1",
            agent_id="agent-1",
            reason="starting",
        )

        mock_te.submit.assert_awaited_once()
        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.IN_PROGRESS
        assert mutation.task_id == "task-1"
        assert mutation.requested_by == "agent-1"
        assert mutation.reason == "starting"

    async def test_rejected_mutation_swallowed(self) -> None:
        """A rejected mutation (success=False) is logged, not raised."""
        mock_te = _make_mock_task_engine(
            return_value=_make_sync_failure(
                error="version conflict",
                error_code="version_conflict",
            ),
        )

        # Should not raise
        await sync_to_task_engine(
            mock_te,
            target_status=TaskStatus.COMPLETED,
            task_id="task-1",
            agent_id="agent-1",
            reason="completing",
        )

        mock_te.submit.assert_awaited_once()

    async def test_rejected_mutation_empty_error_detail(self) -> None:
        """Rejection with empty error uses fallback message."""
        mock_te = _make_mock_task_engine(
            return_value=TaskMutationResult(
                request_id="test",
                success=False,
                error="",
                error_code="validation",
            ),
        )

        await sync_to_task_engine(
            mock_te,
            target_status=TaskStatus.COMPLETED,
            task_id="task-1",
            agent_id="agent-1",
            reason="completing",
        )
        # No exception = fallback message was used for empty string

    async def test_task_engine_error_swallowed(self) -> None:
        """TaskEngineError from submit() is logged and swallowed."""
        mock_te = _make_mock_task_engine(
            side_effect=TaskEngineError("engine down"),
        )

        await sync_to_task_engine(
            mock_te,
            target_status=TaskStatus.IN_PROGRESS,
            task_id="task-1",
            agent_id="agent-1",
            reason="test",
        )

    async def test_unexpected_exception_swallowed(self) -> None:
        """Unexpected RuntimeError from submit() is swallowed."""
        mock_te = _make_mock_task_engine(
            side_effect=RuntimeError("connection lost"),
        )

        await sync_to_task_engine(
            mock_te,
            target_status=TaskStatus.IN_PROGRESS,
            task_id="task-1",
            agent_id="agent-1",
            reason="test",
        )

    @pytest.mark.parametrize(
        ("exc_class", "exc_args"),
        [
            (MemoryError, ("out of memory",)),
            (RecursionError, ("maximum recursion depth exceeded",)),
            (asyncio.CancelledError, ()),
        ],
        ids=["MemoryError", "RecursionError", "CancelledError"],
    )
    async def test_non_swallowed_exception_propagates(
        self,
        exc_class: type[BaseException],
        exc_args: tuple[str, ...],
    ) -> None:
        """Non-recoverable and cancellation exceptions propagate."""
        mock_te = _make_mock_task_engine(
            side_effect=exc_class(*exc_args),
        )

        with pytest.raises(exc_class):
            await sync_to_task_engine(
                mock_te,
                target_status=TaskStatus.IN_PROGRESS,
                task_id="task-1",
                agent_id="agent-1",
                reason="test",
            )

    async def test_critical_flag_logs_at_error_level(self) -> None:
        """critical=True escalates log severity to ERROR."""
        mock_te = _make_mock_task_engine(
            side_effect=TaskEngineError("unavailable"),
        )

        with patch("synthorg.engine.task_sync.logger") as mock_logger:
            await sync_to_task_engine(
                mock_te,
                target_status=TaskStatus.IN_PROGRESS,
                task_id="task-1",
                agent_id="agent-1",
                reason="test",
                critical=True,
            )

        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_not_called()


# ===================================================================
# transition_task_if_needed
# ===================================================================


@pytest.mark.unit
class TestTransitionTaskIfNeeded:
    """Tests for ASSIGNED -> IN_PROGRESS pre-execution transition."""

    async def test_assigned_transitions_to_in_progress(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """ASSIGNED task transitions to IN_PROGRESS and syncs."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert ctx.task_execution is not None
        assert ctx.task_execution.status == TaskStatus.ASSIGNED

        mock_te = _make_mock_task_engine()

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=mock_te,
        )

        assert result_ctx.task_execution is not None
        assert result_ctx.task_execution.status == TaskStatus.IN_PROGRESS
        mock_te.submit.assert_awaited_once()
        assert mock_te.submit.call_args.args[0].target_status == TaskStatus.IN_PROGRESS

    async def test_in_progress_passes_through(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """IN_PROGRESS task is returned as-is (no sync)."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="already started")

        mock_te = _make_mock_task_engine()

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=mock_te,
        )

        assert result_ctx.task_execution is not None
        assert result_ctx.task_execution.status == TaskStatus.IN_PROGRESS
        mock_te.submit.assert_not_awaited()

    async def test_no_task_execution_passes_through(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Context without task_execution returns unchanged."""
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        assert ctx.task_execution is None

        mock_te = _make_mock_task_engine()

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent_with_personality.id),
            task_id="irrelevant",
            task_engine=mock_te,
        )

        assert result_ctx is ctx
        mock_te.submit.assert_not_awaited()

    async def test_none_task_engine_still_transitions_locally(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Local transition works even when task_engine is None."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=None,
        )

        assert result_ctx.task_execution is not None
        assert result_ctx.task_execution.status == TaskStatus.IN_PROGRESS


# ===================================================================
# apply_post_execution_transitions
# ===================================================================


@pytest.mark.unit
class TestApplyPostExecutionTransitions:
    """Tests for post-execution transition logic."""

    async def test_no_task_execution_returns_unchanged(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Without task_execution, result is returned as-is."""
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        result = _make_execution_result(ctx)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id="irrelevant",
            task_engine=None,
        )

        assert out is result

    async def test_completed_transitions_through_in_review_to_completed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """COMPLETED termination: IN_PROGRESS -> IN_REVIEW -> COMPLETED."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.COMPLETED

        # Two syncs: IN_REVIEW and COMPLETED
        assert mock_te.submit.await_count == 2
        synced = [call.args[0].target_status for call in mock_te.submit.call_args_list]
        assert synced == [TaskStatus.IN_REVIEW, TaskStatus.COMPLETED]

    async def test_shutdown_transitions_to_interrupted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """SHUTDOWN termination: current status -> INTERRUPTED."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.SHUTDOWN)

        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.INTERRUPTED
        mock_te.submit.assert_awaited_once()

    @pytest.mark.parametrize(
        "reason",
        [TerminationReason.MAX_TURNS, TerminationReason.BUDGET_EXHAUSTED],
        ids=["MAX_TURNS", "BUDGET_EXHAUSTED"],
    )
    async def test_non_completion_reasons_return_unchanged(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        reason: TerminationReason,
    ) -> None:
        """Non-completion termination reasons leave task state unchanged."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=reason)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=None,
        )

        assert out is result

    async def test_completed_partial_failure_returns_furthest_state(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Partial failure: IN_REVIEW succeeds locally, COMPLETED raises.

        The stepwise loop in ``apply_post_execution_transitions`` captures
        the intermediate ``ctx`` after each successful step.  When the
        second step raises, the returned context reflects IN_REVIEW
        (the furthest-reached state), not the original IN_PROGRESS.
        """
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        # Patch with_task_transition to raise on second call
        call_count = 0
        original_transition = AgentContext.with_task_transition

        def patched_transition(
            self: AgentContext,
            target: TaskStatus,
            *,
            reason: str = "",
        ) -> AgentContext:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                msg = "Simulated transition failure"
                raise ExecutionStateError(msg)
            return original_transition(self, target, reason=reason)

        with patch.object(AgentContext, "with_task_transition", patched_transition):
            mock_te = _make_mock_task_engine()

            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent_with_personality.id),
                task_id=sample_task_with_criteria.id,
                task_engine=mock_te,
            )

            # Context reflects IN_REVIEW — the furthest-reached state
            # before the second transition failed.
            assert out.context.task_execution is not None
            assert out.context.task_execution.status == TaskStatus.IN_REVIEW
            # Result is a model_copy, not the bare original
            assert out is not result

    async def test_shutdown_transition_failure_returns_original(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """SHUTDOWN: if INTERRUPTED transition fails, original result returned."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.SHUTDOWN)

        def raise_on_transition(
            self: AgentContext,
            target: TaskStatus,
            *,
            reason: str = "",
        ) -> AgentContext:
            msg = "cannot interrupt"
            raise ExecutionStateError(msg)

        with patch.object(AgentContext, "with_task_transition", raise_on_transition):
            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent_with_personality.id),
                task_id=sample_task_with_criteria.id,
                task_engine=None,
            )

            # Original result returned when transition fails
            assert out is result
            assert out.context.task_execution is not None
            assert out.context.task_execution.status == TaskStatus.IN_PROGRESS

    async def test_completed_with_none_task_engine(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """COMPLETED path works with task_engine=None (local only)."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=None,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.COMPLETED

    async def test_sync_failure_does_not_block_transitions(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Sync failures (rejected mutations) don't block local transitions."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        # All syncs fail but local transitions should still complete
        mock_te = _make_mock_task_engine(
            return_value=_make_sync_failure(),
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.COMPLETED

    async def test_task_engine_exception_does_not_block_transitions(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """TaskEngineError from submit() doesn't block local transitions."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_te = _make_mock_task_engine(
            side_effect=TaskEngineError("engine down"),
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=sample_task_with_criteria.id,
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.COMPLETED
