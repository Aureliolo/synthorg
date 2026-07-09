"""Unit tests for task_sync module -- AgentEngine → TaskEngine sync functions."""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError, TaskEngineError
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.resume_scope import resumed_run_scope
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    TaskErrorCode,
    TaskMutationResult,
)
from synthorg.engine.task_sync import (
    apply_post_execution_transitions,
    sync_to_task_engine,
    transition_task_if_needed,
)
from synthorg.engine.task_sync_review import _REVIEW_ACTION_TYPE
from synthorg.execution.turn import TurnRecord
from tests._shared import mock_of

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

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


def _make_mock_task_engine(  # type: ignore[explicit-any]  # mock_of returns Any
    side_effect: object | None = None,
    return_value: TaskMutationResult | None = None,
) -> Any:
    """Build a mock TaskEngine with configurable submit behavior."""
    submit = (
        AsyncMock(side_effect=side_effect)
        if side_effect is not None
        else AsyncMock(return_value=return_value or _make_sync_success())
    )
    return mock_of[TaskEngine](submit=submit)


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
                cost=0.001,
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
            task_id=str(sample_task_with_criteria.id),
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
            task_id=str(sample_task_with_criteria.id),
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
            task_id=str(sample_task_with_criteria.id),
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

    async def test_completed_transitions_to_in_review(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """COMPLETED termination: IN_PROGRESS -> IN_REVIEW (awaits review)."""
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
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

        # One sync: IN_REVIEW only (no auto-complete to COMPLETED)
        assert mock_te.submit.await_count == 1
        synced = [call.args[0].target_status for call in mock_te.submit.call_args_list]
        assert synced == [TaskStatus.IN_REVIEW]

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
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.INTERRUPTED
        mock_te.submit.assert_awaited_once()

    @pytest.mark.parametrize(
        "reason",
        [
            TerminationReason.MAX_TURNS,
            TerminationReason.BUDGET_EXHAUSTED,
            TerminationReason.CANCELLED,
        ],
        ids=["MAX_TURNS", "BUDGET_EXHAUSTED", "CANCELLED"],
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
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert out is result

    async def test_error_reason_returns_unchanged(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """ERROR termination reason leaves task state unchanged."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(
            ctx,
            reason=TerminationReason.ERROR,
            error_message="Simulated error",
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert out is result

    async def test_no_op_work_task_transitions_to_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A NO_OP run on a work task fails the task, never pushes to review."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent_with_personality, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(
            ctx, reason=TerminationReason.NO_OP, error_message="empty run"
        )
        mock_te = _make_mock_task_engine()
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
            approval_store=approval_store,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        synced = [c.args[0].target_status for c in mock_te.submit.call_args_list]
        assert synced == [TaskStatus.FAILED]
        approval_store.add.assert_not_awaited()

    async def test_completed_empty_work_task_transitions_to_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Defence-in-depth: a COMPLETED work run with zero tool calls fails."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent_with_personality, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED

    async def test_justified_no_op_goes_to_review(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A recorded no-op justification routes an empty run to review."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent_with_personality, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.NO_OP,
            error_message="empty run",
            metadata={"no_op_justification": "No change needed; code already correct."},
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                ),
            ),
        )
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_completed_work_task_with_tool_calls_goes_to_review(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A COMPLETED work run that used tools proceeds to review as usual."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent_with_personality, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
            turns=(
                TurnRecord(
                    turn_number=1,
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.001,
                    finish_reason=FinishReason.STOP,
                    tool_calls_made=("write_file",),
                ),
            ),
        )
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_resumed_empty_work_run_goes_to_review_not_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A resumed run's zero-tool-call segment must not fail the task.

        Regression (C1): a PARKED-then-resumed run only carries the current
        segment's turns, so its zero-tool-call count is not a valid proxy for
        total output; earlier segments may already have produced artifacts.
        Inside a ``resumed_run_scope`` an otherwise-empty completed work run
        proceeds to review instead of being wrongly failed.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent_with_personality, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)
        mock_te = _make_mock_task_engine()

        with resumed_run_scope():
            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent_with_personality.id),
                task_id=str(work_task.id),
                task_engine=mock_te,
            )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_completed_transition_failure_returns_original(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When IN_REVIEW transition fails, original result is returned.

        Since the only completion step is IN_REVIEW, a failure on
        that step means the context remains at IN_PROGRESS (the
        original state).
        """
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        def raise_on_transition(
            self: AgentContext,
            target: TaskStatus,
            *,
            reason: str = "",
        ) -> AgentContext:
            msg = "Simulated transition failure"
            raise ExecutionStateError(msg)

        with patch.object(AgentContext, "with_task_transition", raise_on_transition):
            mock_te = _make_mock_task_engine()

            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent_with_personality.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=mock_te,
            )

            # Original result returned when transition fails
            assert out is result
            assert out.context.task_execution is not None
            assert out.context.task_execution.status == TaskStatus.IN_PROGRESS

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
                task_id=str(sample_task_with_criteria.id),
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
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

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
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

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
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW


# ===================================================================
# Review approval creation
# ===================================================================


@pytest.mark.unit
class TestReviewApprovalCreation:
    """Tests for review approval auto-creation on IN_REVIEW transition."""

    async def test_creates_approval_with_store(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When approval_store is provided, a review approval is created."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
            approval_store=mock_store,
        )

        mock_store.add.assert_awaited_once()
        item = mock_store.add.call_args.args[0]
        assert item.action_type == _REVIEW_ACTION_TYPE
        assert item.task_id == str(sample_task_with_criteria.id)
        assert item.status == ApprovalStatus.PENDING

    async def test_no_approval_without_store(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When approval_store is None, no approval is created."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
            approval_store=None,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_approval_creation_failure_swallowed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Failure to create approval does not affect task transition."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_store = mock_of[ApprovalStoreProtocol](
            add=AsyncMock(side_effect=RuntimeError("store error"))
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
            approval_store=mock_store,
        )

        # Transition still succeeded despite store error
        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_approval_creation_memory_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        error_cls: type[BaseException],
    ) -> None:
        """MemoryError/RecursionError from approval store propagates."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_store = mock_of[ApprovalStoreProtocol](
            add=AsyncMock(side_effect=error_cls("fatal"))
        )

        with pytest.raises(error_cls):
            await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent_with_personality.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=None,
                approval_store=mock_store,
            )


# ===================================================================
# Automatic review on completion
# ===================================================================


@pytest.mark.unit
class TestAutoReview:
    """Auto-run of the staged review pipeline when a task reaches IN_REVIEW."""

    def _completed_result(self, identity: AgentIdentity, task: Task) -> ExecutionResult:
        ctx = AgentContext.from_identity(identity, task=task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        return _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

    async def test_runs_pipeline_when_gate_and_pipeline_wired(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        result = self._completed_result(
            sample_agent_with_personality, sample_task_with_criteria
        )
        gate = mock_of[ReviewGateService](run_pipeline=AsyncMock(return_value=None))
        pipeline = mock_of[ReviewPipeline]()

        await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            review_gate=gate,
            review_pipeline=pipeline,
        )

        gate.run_pipeline.assert_awaited_once()
        call = gate.run_pipeline.await_args
        assert call.kwargs["task_id"] == str(sample_task_with_criteria.id)
        assert call.kwargs["pipeline"] is pipeline
        assert call.kwargs["decided_by"] == "system:auto-review"

    async def test_no_pipeline_run_when_disabled(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        # Gate wired but no pipeline (auto-review off): the pipeline never runs.
        result = self._completed_result(
            sample_agent_with_personality, sample_task_with_criteria
        )
        gate = mock_of[ReviewGateService](run_pipeline=AsyncMock(return_value=None))

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            review_gate=gate,
            review_pipeline=None,
        )

        gate.run_pipeline.assert_not_called()
        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_pipeline_failure_does_not_discard_completion(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        # A review-pipeline fault must not lose the agent's completed work:
        # the task stays IN_REVIEW for a human, exactly as when off.
        result = self._completed_result(
            sample_agent_with_personality, sample_task_with_criteria
        )
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(side_effect=RuntimeError("pipeline boom"))
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            review_gate=gate,
            review_pipeline=mock_of[ReviewPipeline](),
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW


@pytest.mark.unit
class TestClarificationPark:
    """A clarification park moves an executing task to AWAITING_INPUT."""

    def _parked_result(
        self,
        identity: AgentIdentity,
        task: Task,
        *,
        clarification: bool,
    ) -> ExecutionResult:
        ctx = AgentContext.from_identity(identity, task=task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        return ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.PARKED,
            metadata={"approval_id": "appr-1", "clarification": clarification},
        )

    async def test_clarification_park_transitions_to_awaiting_input(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        result = self._parked_result(
            sample_agent_with_personality, sample_task_with_criteria, clarification=True
        )
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.AWAITING_INPUT
        synced = [call.args[0].target_status for call in mock_te.submit.call_args_list]
        assert synced == [TaskStatus.AWAITING_INPUT]

    async def test_plain_approval_park_leaves_task_unchanged(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        # A binary approval park (no clarification marker) leaves the task
        # IN_PROGRESS -- distinct from the clarification pause.
        result = self._parked_result(
            sample_agent_with_personality,
            sample_task_with_criteria,
            clarification=False,
        )
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent_with_personality.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out is result
        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_PROGRESS
        mock_te.submit.assert_not_called()
