"""Unit tests for task_sync module -- AgentEngine → TaskEngine sync functions."""

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine._review_oracle_gates import GateOutcome
from synthorg.engine._task_sync_engine import sync_to_task_engine
from synthorg.engine.artifacts.baseline_scope import artifact_baseline_scope
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    ExpectedArtifactProbe,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError, TaskEngineError
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.loop_rework import REWORK_METADATA_KEY
from synthorg.engine.resume_scope import resumed_run_scope
from synthorg.engine.review.models import PipelineResult, ReviewVerdict
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate import ReviewGateService, ReviewRun
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    TaskErrorCode,
    TaskMutationResult,
)
from synthorg.engine.task_sync import (
    apply_post_execution_transitions,
    transition_task_if_needed,
)
from synthorg.engine.task_sync_review import _REVIEW_ACTION_TYPE
from synthorg.execution.turn import TurnRecord
from tests._shared import mock_of

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task


def _fake_probe(
    *,
    missing: tuple[str, ...] | None = None,
    digest: str | None = None,
) -> ExpectedArtifactProbe:
    """Build a probe reporting *missing* against whatever was declared.

    Args:
        missing: The declared paths to report absent. ``None`` reports every
            declared path absent, which is the delivered-nothing case.
        digest: Content digest to report for every present declaration, so a
            caller can pair a pre-run answer with a post-run one and exercise
            the changed / unchanged distinction.

    Returns:
        An async probe matching the ``ExpectedArtifactProbe`` shape.
    """

    async def _run(
        _project: str, expected: Sequence[ExpectedArtifact]
    ) -> ArtifactPresence:
        declared = tuple(str(artifact.path) for artifact in expected)
        absent = declared if missing is None else missing
        return ArtifactPresence(
            probed=declared,
            missing=absent,
            digests=(
                {}
                if digest is None
                else {path: digest for path in declared if path not in absent}
            ),
        )

    return _run


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


def _make_execution_result_with_tool_calls(
    ctx: AgentContext,
    reason: TerminationReason = TerminationReason.COMPLETED,
) -> ExecutionResult:
    """Build a run that made tool calls but may have produced nothing.

    This is the shape the zero-tool-call proxy waves through: the agent
    read files, so the count is non-zero, but no deliverable need exist.

    Returns:
        A completed run carrying one turn with two tool calls.
    """
    return ExecutionResult(
        context=ctx,
        termination_reason=reason,
        turns=(
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                finish_reason=FinishReason.STOP,
                tool_calls_made=(NotBlankStr("read_file"), NotBlankStr("read_file")),
            ),
        ),
    )


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

        with patch("synthorg.engine._task_sync_engine.logger") as mock_logger:
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
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """ASSIGNED task transitions to IN_PROGRESS and syncs."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        assert ctx.task_execution is not None
        assert ctx.task_execution.status == TaskStatus.ASSIGNED

        mock_te = _make_mock_task_engine()

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert result_ctx.task_execution is not None
        assert result_ctx.task_execution.status == TaskStatus.IN_PROGRESS
        mock_te.submit.assert_awaited_once()
        assert mock_te.submit.call_args.args[0].target_status == TaskStatus.IN_PROGRESS

    async def test_in_progress_passes_through(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """IN_PROGRESS task is returned as-is (no sync)."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="already started")

        mock_te = _make_mock_task_engine()

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert result_ctx.task_execution is not None
        assert result_ctx.task_execution.status == TaskStatus.IN_PROGRESS
        mock_te.submit.assert_not_awaited()

    async def test_no_task_execution_passes_through(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        """Context without task_execution returns unchanged."""
        ctx = AgentContext.from_identity(sample_agent)
        assert ctx.task_execution is None

        mock_te = _make_mock_task_engine()

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent.id),
            task_id="irrelevant",
            task_engine=mock_te,
        )

        assert result_ctx is ctx
        mock_te.submit.assert_not_awaited()

    async def test_none_task_engine_still_transitions_locally(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Local transition works even when task_engine is None."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )

        result_ctx = await transition_task_if_needed(
            ctx,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert result_ctx.task_execution is not None
        assert result_ctx.task_execution.status == TaskStatus.IN_PROGRESS

    async def test_refused_entry_sync_aborts_the_run(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A rejected entry sync stops the run instead of proceeding unsynced.

        The central engine still holds the task at its prior status, so
        running would produce work against a row the engine has no record
        of moving.
        """
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        mock_te = _make_mock_task_engine(
            return_value=_make_sync_failure(error="task is still created"),
        )

        with pytest.raises(ExecutionStateError, match="IN_PROGRESS"):
            await transition_task_if_needed(
                ctx,
                agent_id=str(sample_agent.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=mock_te,
            )

    async def test_unavailable_engine_aborts_the_run(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """An engine that raised is as unsynced as one that refused."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        mock_te = _make_mock_task_engine(side_effect=TaskEngineError("unavailable"))

        with pytest.raises(ExecutionStateError):
            await transition_task_if_needed(
                ctx,
                agent_id=str(sample_agent.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=mock_te,
            )


# ===================================================================
# apply_post_execution_transitions
# ===================================================================


@pytest.mark.unit
class TestApplyPostExecutionTransitions:
    """Tests for post-execution transition logic."""

    async def test_no_task_execution_returns_unchanged(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        """Without task_execution, result is returned as-is."""
        ctx = AgentContext.from_identity(sample_agent)
        result = _make_execution_result(ctx)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id="irrelevant",
            task_engine=None,
        )

        assert out is result

    async def test_completed_transitions_to_in_review(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """COMPLETED termination: IN_PROGRESS -> IN_REVIEW (awaits review)."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
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
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """SHUTDOWN termination: current status -> INTERRUPTED."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.SHUTDOWN)

        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.INTERRUPTED
        mock_te.submit.assert_awaited_once()

    @pytest.mark.parametrize(
        "reason",
        [TerminationReason.CANCELLED],
        ids=["CANCELLED"],
    )
    async def test_already_terminal_reasons_return_unchanged(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
        reason: TerminationReason,
    ) -> None:
        """An already-terminal reason needs no transition of its own.

        The reasons that stop *without* finishing (turn cap, budget,
        stagnation) do get terminalised; see
        ``test_unfinished_run_terminalises_to_failed``.
        """
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=reason)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert out is result

    async def test_error_reason_returns_unchanged(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """ERROR termination reason leaves task state unchanged."""
        ctx = AgentContext.from_identity(
            sample_agent,
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
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert out is result

    async def test_no_op_work_task_transitions_to_failed(
        self,
        sample_agent: AgentIdentity,
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
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(
            ctx, reason=TerminationReason.NO_OP, error_message="empty run"
        )
        mock_te = _make_mock_task_engine()
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
            approval_store=approval_store,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        synced = [c.args[0].target_status for c in mock_te.submit.call_args_list]
        assert synced == [TaskStatus.FAILED]
        # The failed run surfaces in the approval queue as a distinct failure
        # (action type review:task_failed), escalated above LOW so it is never
        # a routine low-risk approval.
        approval_store.add.assert_awaited_once()
        created = approval_store.add.await_args.args[0]
        assert created.action_type == "review:task_failed"
        assert created.risk_level == ApprovalRiskLevel.HIGH

    async def test_failed_approval_skipped_when_central_sync_rejected(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """No failure approval is queued when the engine rejects the FAILED sync.

        A swallowed/rejected central transition leaves the engine's task
        IN_PROGRESS; a ``review:task_failed`` item pointing at it would let a
        later decision transition the wrong state, so creation is gated on the
        sync landing.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(
            ctx, reason=TerminationReason.NO_OP, error_message="empty run"
        )
        mock_te = _make_mock_task_engine(return_value=_make_sync_failure())
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
            approval_store=approval_store,
        )

        # Local state still reflects FAILED (the local transition is applied
        # unconditionally), but the approval is withheld because the engine did
        # not accept the transition.
        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        approval_store.add.assert_not_awaited()

    async def test_completed_empty_work_task_transitions_to_failed(
        self,
        sample_agent: AgentIdentity,
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
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED

    async def test_justified_no_op_goes_to_review(
        self,
        sample_agent: AgentIdentity,
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
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
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
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_completed_work_task_with_tool_calls_goes_to_review(
        self,
        sample_agent: AgentIdentity,
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
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
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
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_resumed_empty_work_run_goes_to_review_not_failed(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A resumed run's zero-tool-call segment must not fail the task.

        A PARKED-then-resumed run only carries the current segment's turns,
        so its zero-tool-call count is not a valid proxy for total output;
        earlier segments may already have produced artifacts.
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
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)
        mock_te = _make_mock_task_engine()

        with resumed_run_scope():
            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent.id),
                task_id=str(work_task.id),
                task_engine=mock_te,
            )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_resumed_run_still_answers_to_the_workspace(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The resume exemption covers the turn-count proxy, not the disk.

        A resumed segment's zero tool calls say nothing about earlier
        segments, which is why the proxy is exempted. The filesystem has no
        such blind spot: whatever an earlier segment produced is still
        there. So a resumed run with none of its declared paths present
        delivered nothing, and exempting it too would let any task reach
        review by being resumed once."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)

        async def _nothing_delivered(
            _project: str, _expected: Sequence[ExpectedArtifact]
        ) -> ArtifactPresence:
            return ArtifactPresence(probed=("src/x.py",), missing=("src/x.py",))

        with resumed_run_scope():
            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent.id),
                task_id=str(work_task.id),
                task_engine=_make_mock_task_engine(),
                artifact_probe=_nothing_delivered,
            )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED

    @pytest.mark.parametrize(
        ("reason", "expected_fragment"),
        [
            (TerminationReason.MAX_TURNS, "turn cap"),
            (TerminationReason.BUDGET_EXHAUSTED, "cost budget"),
            (TerminationReason.STAGNATION, "stopped making progress"),
        ],
    )
    async def test_unfinished_run_terminalises_to_failed(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
        reason: TerminationReason,
        expected_fragment: str,
    ) -> None:
        """A run that stopped without finishing must not sit at IN_PROGRESS.

        The stall derivation reads IN_PROGRESS as "still moving", so a task
        left there is permanently un-stalled, un-replanned and
        un-completable. FAILED is honest, retryable, and the status the
        derivation recognises once retries are spent.
        """
        ctx = AgentContext.from_identity(sample_agent, task=sample_task_with_criteria)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=reason)
        mock_te = _make_mock_task_engine()
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
            approval_store=approval_store,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        # The termination reason is recorded, so an operator reads why the
        # run stopped rather than an undifferentiated failure.
        submitted = mock_te.submit.call_args_list[0].args[0]
        assert expected_fragment in submitted.reason
        approval_store.add.assert_awaited_once()

    async def test_a_failed_transition_that_cannot_land_is_raised(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Swallowing it would leave the task at its prior status silently.

        That is the exact invisibility this path exists to close, so the
        error goes to the caller instead. No failure approval is queued
        either: the task never reached FAILED, and an approval saying it did
        would be a second lie on top of the first.

        IN_REVIEW is the state that makes the transition impossible: the
        machine has no IN_REVIEW -> FAILED edge, so the local move raises
        before any sync is attempted.
        """
        ctx = AgentContext.from_identity(sample_agent, task=sample_task_with_criteria)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        ctx = ctx.with_task_transition(TaskStatus.IN_REVIEW, reason="submitted")
        result = _make_execution_result(ctx, reason=TerminationReason.MAX_TURNS)
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        with pytest.raises(ExecutionStateError):
            await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=_make_mock_task_engine(),
                approval_store=approval_store,
            )

        approval_store.add.assert_not_awaited()

    async def test_a_failure_the_engine_never_applied_queues_no_approval(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A swallowed sync leaves the engine's task at its prior status.

        A ``review:task_failed`` item pointing at a task the engine still
        holds IN_PROGRESS would let a later decision transition the wrong
        state, so the approval waits on the sync landing.
        """
        ctx = AgentContext.from_identity(sample_agent, task=sample_task_with_criteria)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.MAX_TURNS)
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(
                side_effect=TaskEngineError("engine unavailable")
            ),
            approval_store=approval_store,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        approval_store.add.assert_not_awaited()

    async def test_parked_run_still_holds_its_status(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """PARKED waits on a human; terminalising it would discard the wait."""
        ctx = AgentContext.from_identity(sample_agent, task=sample_task_with_criteria)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.PARKED)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
        )

        assert out is result

    async def test_declared_artifacts_all_absent_fails_despite_tool_calls(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The tool-call proxy passes; the workspace says nothing was written.

        An agent that reads two files, writes nothing and stops has made
        tool calls, so the proxy classifies it as productive. Asking the
        workspace is what catches it.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)
        mock_te = _make_mock_task_engine()
        approval_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=mock_te,
            approval_store=approval_store,
            artifact_probe=_fake_probe(),
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        submitted = mock_te.submit.call_args_list[0].args[0]
        assert "src/x.py" in submitted.reason

    async def test_an_untouched_declaration_is_a_no_op_not_a_delivery(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Presence alone answers a task that creates, never one that edits.

        A bugfix task finds its declared file already there, so the run comes
        back "delivered" whatever it did. Only the baseline separates a run
        that fixed the file from one that read it and stopped.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)
        found_as_seeded = ArtifactPresence(
            probed=("src/x.py",), missing=(), digests={"src/x.py": "before"}
        )

        with artifact_baseline_scope(found_as_seeded):
            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent.id),
                task_id=str(work_task.id),
                task_engine=_make_mock_task_engine(),
                approval_store=mock_of[ApprovalStoreProtocol](add=AsyncMock()),
                artifact_probe=_fake_probe(missing=(), digest="before"),
            )

        assert out.termination_reason == TerminationReason.NO_OP
        assert out.error_message is not None
        assert "src/x.py" in out.error_message

    async def test_a_changed_declaration_is_delivery(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The guard must not fail a run that actually edited what it declared.

        This is the direction that costs a real run its result if it is wrong,
        and it is invisible to the failing direction's tests: both start from a
        declaration that is present before the agent runs.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)
        found_as_seeded = ArtifactPresence(
            probed=("src/x.py",), missing=(), digests={"src/x.py": "before"}
        )

        with artifact_baseline_scope(found_as_seeded):
            out = await apply_post_execution_transitions(
                result,
                agent_id=str(sample_agent.id),
                task_id=str(work_task.id),
                task_engine=_make_mock_task_engine(),
                approval_store=mock_of[ApprovalStoreProtocol](add=AsyncMock()),
                artifact_probe=_fake_probe(missing=(), digest="after"),
            )

        assert out.termination_reason == TerminationReason.COMPLETED

    async def test_a_failed_run_does_not_still_report_itself_completed(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The task and the run must not disagree about whether it worked.

        The loop says ``COMPLETED`` and the workspace says every declared path
        is absent, so this transition fails the task. Leaving the run's own
        reason at ``COMPLETED`` would keep ``AgentRunResult.is_success``
        true, and every caller that gates on it -- delegation, coordination,
        the plan rollup -- would read the failure as a success.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)
        assert result.termination_reason == TerminationReason.COMPLETED

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=_make_mock_task_engine(),
            approval_store=mock_of[ApprovalStoreProtocol](add=AsyncMock()),
            artifact_probe=_fake_probe(),
        )

        assert out.termination_reason == TerminationReason.NO_OP
        # The adjudicated result is re-validated the moment the engine wraps it
        # in an AgentRunResult, and NO_OP without an error_message is rejected
        # there. Copying the reason on without one turned every caught run into
        # a ValidationError, which the engine then recorded as a zero-turn
        # ERROR: the guard fired and the evidence of it firing was destroyed.
        assert out.error_message is not None
        assert "src/x.py" in out.error_message
        # ``model_copy`` does not re-validate, so the reason/error_message
        # pairing has to be asserted through a real construction. This is the
        # check the engine performs when it wraps the run in an
        # ``AgentRunResult``, and it is where the incomplete copy blew up.
        ExecutionResult(
            context=out.context,
            termination_reason=out.termination_reason,
            turns=out.turns,
            error_message=out.error_message,
        )

    async def test_an_unfinished_run_keeps_the_reason_it_stopped_for(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Adjudication supplies a reason only where there was none to lose.

        A run that hit its turn ceiling already answers "did it work" with
        ``False``; overwriting it with ``NO_OP`` would throw away the more
        specific fact of *how* it stopped.
        """
        ctx = AgentContext.from_identity(sample_agent, task=sample_task_with_criteria)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.MAX_TURNS,
            turns=(),
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            approval_store=mock_of[ApprovalStoreProtocol](add=AsyncMock()),
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.FAILED
        assert out.termination_reason == TerminationReason.MAX_TURNS

    async def test_partial_delivery_still_reaches_review(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """One file elsewhere is a judgement call, not an empty run.

        The threshold is deliberately "none of them present": an agent that
        legitimately chose a different path for one file should reach the
        completion oracle, which can judge the substitution.
        """
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                    ExpectedArtifact(type=ArtifactType.TESTS, path="tests/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=_make_mock_task_engine(),
            artifact_probe=_fake_probe(missing=("tests/x.py",)),
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_delivered_artifacts_reach_review(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=_make_mock_task_engine(),
            artifact_probe=_fake_probe(missing=()),
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_unanswerable_probe_does_not_fail_the_task(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A probe that cannot answer is not evidence of an empty run.

        Failing here would discard genuinely delivered work whenever a
        volume blips, so the run proceeds to review. It does not proceed
        unlabelled: the same fault makes the deliverable reader emit
        ``produced_artifacts.status == "not_verified"`` (covered in
        ``test_review_gate_inputs``), so the fail-CLOSED peer reviewer sees
        an unverified deliverable rather than prose it might mistake for
        evidence. The gap this leaves is a reviewer decision, not a silent
        pass."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                )
            }
        )
        ctx = AgentContext.from_identity(sample_agent, task=work_task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result_with_tool_calls(ctx)

        async def _raises(
            _project: str, _expected: Sequence[ExpectedArtifact]
        ) -> ArtifactPresence:
            msg = "workspace volume unavailable"
            raise OSError(msg)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(work_task.id),
            task_engine=_make_mock_task_engine(),
            artifact_probe=_raises,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_completed_transition_failure_returns_original(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When IN_REVIEW transition fails, original result is returned.

        Since the only completion step is IN_REVIEW, a failure on
        that step means the context remains at IN_PROGRESS (the
        original state).
        """
        ctx = AgentContext.from_identity(
            sample_agent,
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
                agent_id=str(sample_agent.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=mock_te,
            )

            # Original result returned when transition fails
            assert out is result
            assert out.context.task_execution is not None
            assert out.context.task_execution.status == TaskStatus.IN_PROGRESS

    async def test_shutdown_transition_failure_returns_original(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """SHUTDOWN: if INTERRUPTED transition fails, original result returned."""
        ctx = AgentContext.from_identity(
            sample_agent,
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
                agent_id=str(sample_agent.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=None,
            )

            # Original result returned when transition fails
            assert out is result
            assert out.context.task_execution is not None
            assert out.context.task_execution.status == TaskStatus.IN_PROGRESS

    async def test_completed_with_none_task_engine(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """COMPLETED path works with task_engine=None (local only)."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_sync_failure_does_not_block_transitions(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Sync failures (rejected mutations) don't block local transitions."""
        ctx = AgentContext.from_identity(
            sample_agent,
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
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_task_engine_exception_does_not_block_transitions(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """TaskEngineError from submit() doesn't block local transitions."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_te = _make_mock_task_engine(
            side_effect=TaskEngineError("engine down"),
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
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
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When approval_store is provided, a review approval is created."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())

        await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
            approval_store=mock_store,
        )

        mock_store.add.assert_awaited_once()
        item = mock_store.add.call_args.args[0]
        assert item.action_type == _REVIEW_ACTION_TYPE
        assert item.task_id == str(sample_task_with_criteria.id)
        assert item.status == ApprovalStatus.PENDING

    async def test_skips_approval_when_review_sync_rejected(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """No review approval is queued when the engine rejects the IN_REVIEW sync.

        A swallowed/rejected central transition leaves the engine's task
        IN_PROGRESS; an approval against that stale state would let a later
        decision act on a status the engine has not applied.
        """
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)
        mock_store = mock_of[ApprovalStoreProtocol](add=AsyncMock())
        mock_te = _make_mock_task_engine(return_value=_make_sync_failure())

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
            approval_store=mock_store,
        )

        # Local state still reflects IN_REVIEW (the local transition is applied
        # unconditionally), but the approval is withheld because the engine did
        # not accept the transition.
        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW
        mock_store.add.assert_not_awaited()

    async def test_no_approval_without_store(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When approval_store is None, no approval is created."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=None,
            approval_store=None,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_REVIEW

    async def test_approval_creation_failure_swallowed(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Failure to create approval does not affect task transition."""
        ctx = AgentContext.from_identity(
            sample_agent,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        result = _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

        mock_store = mock_of[ApprovalStoreProtocol](
            add=AsyncMock(side_effect=RuntimeError("store error"))
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
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
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
        error_cls: type[BaseException],
    ) -> None:
        """MemoryError/RecursionError from approval store propagates."""
        ctx = AgentContext.from_identity(
            sample_agent,
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
                agent_id=str(sample_agent.id),
                task_id=str(sample_task_with_criteria.id),
                task_engine=None,
                approval_store=mock_store,
            )


# ===================================================================
# Automatic review on completion
# ===================================================================


def _review_run(outcome: GateOutcome | None) -> ReviewRun:
    """A review that produced *outcome*, with a passing pipeline result."""
    return ReviewRun(
        result=PipelineResult(
            task_id=NotBlankStr("task-1"),
            final_verdict=ReviewVerdict.PASS,
        ),
        outcome=outcome,
    )


def _approving_review_run() -> ReviewRun:
    """A review that accepted the work, so nothing is sent back."""
    return _review_run(
        GateOutcome(
            target=TaskStatus.COMPLETED,
            transition_reason="approved",
            event="test.review.completed",
            approved=True,
        )
    )


def _rework_review_run(reason: str) -> ReviewRun:
    """A review that refused the work and sent it back for rework."""
    return _review_run(
        GateOutcome(
            target=TaskStatus.IN_PROGRESS,
            transition_reason=reason,
            event="test.review.rework",
            approved=False,
        )
    )


@pytest.mark.unit
class TestAutoReview:
    """Auto-run of the staged review pipeline when a task reaches IN_REVIEW."""

    def _completed_result(self, identity: AgentIdentity, task: Task) -> ExecutionResult:
        ctx = AgentContext.from_identity(identity, task=task)
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
        return _make_execution_result(ctx, reason=TerminationReason.COMPLETED)

    async def test_runs_pipeline_when_gate_and_pipeline_wired(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        result = self._completed_result(sample_agent, sample_task_with_criteria)
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(return_value=_approving_review_run())
        )
        pipeline = mock_of[ReviewPipeline]()

        await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
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

    async def test_a_review_that_sends_the_work_back_says_so_on_the_run(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """REWORK means "run this again", so it has to reach something runnable.

        The wave that ran this task has returned and nothing polls IN_PROGRESS,
        so the only party that can act is the dispatch still holding the loop.
        Writing the status and nothing else is what left five tasks of a live
        plan in a state no reader was watching.
        """
        result = self._completed_result(sample_agent, sample_task_with_criteria)
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(
                return_value=_rework_review_run("no test run; unverified")
            )
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            review_gate=gate,
            review_pipeline=mock_of[ReviewPipeline](),
        )

        assert out.metadata[REWORK_METADATA_KEY] == "no test run; unverified"

    async def test_an_accepted_review_leaves_no_rework_to_answer(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        result = self._completed_result(sample_agent, sample_task_with_criteria)
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(return_value=_approving_review_run())
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            review_gate=gate,
            review_pipeline=mock_of[ReviewPipeline](),
        )

        assert REWORK_METADATA_KEY not in out.metadata

    async def test_a_park_is_not_a_rework(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """An escalation waits on a human; re-running answers nobody."""
        result = self._completed_result(sample_agent, sample_task_with_criteria)
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(
                return_value=_review_run(
                    GateOutcome(
                        target=TaskStatus.BLOCKED,
                        transition_reason="escalated to a human",
                        event="test.review.escalated",
                        approved=False,
                    )
                )
            )
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=_make_mock_task_engine(),
            review_gate=gate,
            review_pipeline=mock_of[ReviewPipeline](),
        )

        assert REWORK_METADATA_KEY not in out.metadata

    async def test_no_pipeline_run_when_disabled(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        # Gate wired but no pipeline (auto-review off): the pipeline never runs.
        result = self._completed_result(sample_agent, sample_task_with_criteria)
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(return_value=_approving_review_run())
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
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
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        # A review-pipeline fault must not lose the agent's completed work:
        # the task stays IN_REVIEW for a human, exactly as when off.
        result = self._completed_result(sample_agent, sample_task_with_criteria)
        gate = mock_of[ReviewGateService](
            run_pipeline=AsyncMock(side_effect=RuntimeError("pipeline boom"))
        )

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
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
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        result = self._parked_result(
            sample_agent, sample_task_with_criteria, clarification=True
        )
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.AWAITING_INPUT
        synced = [call.args[0].target_status for call in mock_te.submit.call_args_list]
        assert synced == [TaskStatus.AWAITING_INPUT]

    async def test_plain_approval_park_leaves_task_unchanged(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        # A binary approval park (no clarification marker) leaves the task
        # IN_PROGRESS -- distinct from the clarification pause.
        result = self._parked_result(
            sample_agent,
            sample_task_with_criteria,
            clarification=False,
        )
        mock_te = _make_mock_task_engine()

        out = await apply_post_execution_transitions(
            result,
            agent_id=str(sample_agent.id),
            task_id=str(sample_task_with_criteria.id),
            task_engine=mock_te,
        )

        assert out is result
        assert out.context.task_execution is not None
        assert out.context.task_execution.status == TaskStatus.IN_PROGRESS
        mock_te.submit.assert_not_called()
