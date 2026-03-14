"""Tests for CheckpointRecoveryStrategy."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.enums import TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.checkpoint.models import Checkpoint, CheckpointConfig
from synthorg.engine.checkpoint.strategy import CheckpointRecoveryStrategy
from synthorg.engine.context import AgentContext
from synthorg.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
    RecoveryStrategy,
)
from synthorg.persistence.errors import QueryError

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.task_execution import TaskExecution

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_checkpoint(
    *,
    execution_id: str = "exec-001",
    turn_number: int = 3,
    context_json: str = '{"state": "resumed"}',
) -> Checkpoint:
    """Build a Checkpoint with sensible defaults."""
    return Checkpoint(
        execution_id=execution_id,
        agent_id="agent-001",
        task_id="task-001",
        turn_number=turn_number,
        context_json=context_json,
    )


def _make_mock_repo(
    checkpoint: Checkpoint | None = None,
    *,
    error: Exception | None = None,
) -> AsyncMock:
    """Build a mock CheckpointRepository."""
    repo = AsyncMock()
    if error is not None:
        repo.get_latest = AsyncMock(side_effect=error)
    else:
        repo.get_latest = AsyncMock(return_value=checkpoint)
    return repo


def _make_strategy(
    repo: AsyncMock,
    *,
    config: CheckpointConfig | None = None,
    fallback: RecoveryStrategy | None = None,
) -> CheckpointRecoveryStrategy:
    """Build a CheckpointRecoveryStrategy."""
    return CheckpointRecoveryStrategy(
        checkpoint_repo=repo,
        config=config or CheckpointConfig(),
        fallback=fallback,
    )


def _make_in_progress_ctx(
    agent: AgentIdentity,
    task: Task,
) -> tuple[AgentContext, TaskExecution]:
    """Build a context with IN_PROGRESS task execution."""
    ctx = AgentContext.from_identity(agent, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
    assert ctx.task_execution is not None
    return ctx, ctx.task_execution


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckpointRecoveryProtocol:
    """CheckpointRecoveryStrategy satisfies RecoveryStrategy protocol."""

    def test_is_runtime_checkable(self) -> None:
        repo = _make_mock_repo()
        strategy = _make_strategy(repo)
        assert isinstance(strategy, RecoveryStrategy)

    def test_get_strategy_type(self) -> None:
        repo = _make_mock_repo()
        strategy = _make_strategy(repo)
        assert strategy.get_strategy_type() == "checkpoint"


@pytest.mark.unit
class TestCheckpointRecoveryResume:
    """Resume from a valid checkpoint."""

    async def test_resume_with_valid_checkpoint(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Returns RecoveryResult with can_resume=True when checkpoint exists."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        strategy = _make_strategy(repo)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="LLM crashed",
            context=ctx,
        )

        assert isinstance(result, RecoveryResult)
        assert result.can_resume is True
        assert result.checkpoint_context_json == checkpoint.context_json
        assert result.strategy_type == "checkpoint"
        assert result.error_message == "LLM crashed"
        assert result.resume_attempt == 1

    async def test_task_not_transitioned_to_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Task execution is NOT transitioned to FAILED (unlike FailAndReassign)."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        strategy = _make_strategy(repo)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        # The checkpoint strategy preserves the original task_execution
        # (still IN_PROGRESS), not transitioning to FAILED.
        assert result.task_execution.status is TaskStatus.IN_PROGRESS


@pytest.mark.unit
class TestCheckpointRecoveryFallback:
    """Fallback to FailAndReassignStrategy when no checkpoint or max attempts."""

    async def test_no_checkpoint_delegates_to_fallback(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Falls back when no checkpoint found."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        repo = _make_mock_repo(checkpoint=None)
        strategy = _make_strategy(repo)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        # Fallback is FailAndReassignStrategy which transitions to FAILED
        assert result.task_execution.status is TaskStatus.FAILED
        assert result.strategy_type == "fail_reassign"
        assert result.can_resume is False

    async def test_max_resume_attempts_exhausted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Falls back after max_resume_attempts reached."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        config = CheckpointConfig(max_resume_attempts=2)
        strategy = _make_strategy(repo, config=config)

        # First two recoveries succeed (resume_attempt 1 and 2)
        result1 = await strategy.recover(
            task_execution=task_exec,
            error_message="crash 1",
            context=ctx,
        )
        assert result1.can_resume is True
        assert result1.resume_attempt == 1

        result2 = await strategy.recover(
            task_execution=task_exec,
            error_message="crash 2",
            context=ctx,
        )
        assert result2.can_resume is True
        assert result2.resume_attempt == 2

        # Third recovery should fall back
        result3 = await strategy.recover(
            task_execution=task_exec,
            error_message="crash 3",
            context=ctx,
        )
        assert result3.can_resume is False
        assert result3.strategy_type == "fail_reassign"

    async def test_zero_max_resume_attempts_always_fallback(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """max_resume_attempts=0 always falls back."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        config = CheckpointConfig(max_resume_attempts=0)
        strategy = _make_strategy(repo, config=config)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        assert result.can_resume is False
        assert result.strategy_type == "fail_reassign"

    async def test_repo_error_delegates_to_fallback(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Falls back when checkpoint repo raises an exception."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        repo = _make_mock_repo(error=QueryError("DB connection lost"))
        strategy = _make_strategy(repo)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        assert result.can_resume is False
        assert result.strategy_type == "fail_reassign"
        assert result.task_execution.status is TaskStatus.FAILED

    async def test_custom_fallback_used(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Custom fallback strategy is used when provided."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        repo = _make_mock_repo(checkpoint=None)

        mock_fallback = MagicMock(spec=FailAndReassignStrategy)
        snapshot = ctx.to_snapshot()
        fallback_result = RecoveryResult(
            task_execution=task_exec.with_transition(
                TaskStatus.FAILED, reason="custom"
            ),
            strategy_type="custom_fallback",
            context_snapshot=snapshot,
            error_message="crash",
        )
        mock_fallback.recover = AsyncMock(return_value=fallback_result)

        strategy = _make_strategy(repo, fallback=mock_fallback)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        assert result.strategy_type == "custom_fallback"
        mock_fallback.recover.assert_awaited_once()


@pytest.mark.unit
class TestCheckpointRecoveryCounter:
    """Resume counter tracking and reset."""

    async def test_counter_increments_per_execution(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Resume attempts are tracked per execution_id."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        config = CheckpointConfig(max_resume_attempts=5)
        strategy = _make_strategy(repo, config=config)

        result1 = await strategy.recover(
            task_execution=task_exec,
            error_message="crash 1",
            context=ctx,
        )
        assert result1.resume_attempt == 1

        result2 = await strategy.recover(
            task_execution=task_exec,
            error_message="crash 2",
            context=ctx,
        )
        assert result2.resume_attempt == 2

    async def test_clear_resume_count_resets(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """clear_resume_count resets the counter for an execution."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        config = CheckpointConfig(max_resume_attempts=5)
        strategy = _make_strategy(repo, config=config)

        # Use up one attempt
        await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        # Clear and retry
        await strategy.clear_resume_count(ctx.execution_id)

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash again",
            context=ctx,
        )
        assert result.resume_attempt == 1  # Reset to 1, not 2

    async def test_clear_resume_count_noop_for_unknown(self) -> None:
        """Clearing a nonexistent execution_id is a safe no-op."""
        repo = _make_mock_repo()
        strategy = _make_strategy(repo)
        await strategy.clear_resume_count("nonexistent-exec")  # Should not raise

    async def test_independent_counters_per_execution(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Different execution IDs have independent counters."""
        task_a = Task(
            id="task-a",
            title="Task A",
            description="First task",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            status=TaskStatus.ASSIGNED,
        )
        task_b = Task(
            id="task-b",
            title="Task B",
            description="Second task",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            status=TaskStatus.ASSIGNED,
        )

        ctx_a, exec_a = _make_in_progress_ctx(sample_agent_with_personality, task_a)
        ctx_b, exec_b = _make_in_progress_ctx(sample_agent_with_personality, task_b)

        cp_a = _make_checkpoint(execution_id=ctx_a.execution_id)
        cp_b = _make_checkpoint(execution_id=ctx_b.execution_id)

        repo = AsyncMock()
        repo.get_latest = AsyncMock(
            side_effect=lambda execution_id=None, task_id=None: (
                cp_a if execution_id == ctx_a.execution_id else cp_b
            )
        )
        config = CheckpointConfig(max_resume_attempts=5)
        strategy = _make_strategy(repo, config=config)

        result_a = await strategy.recover(
            task_execution=exec_a,
            error_message="crash a",
            context=ctx_a,
        )
        assert result_a.resume_attempt == 1

        result_b = await strategy.recover(
            task_execution=exec_b,
            error_message="crash b",
            context=ctx_b,
        )
        assert result_b.resume_attempt == 1  # Independent counter


@pytest.mark.unit
class TestCheckpointRecoveryFinalize:
    """finalize() delegates to clear_resume_count."""

    async def test_finalize_clears_counter(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """finalize() resets the counter for the execution."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        repo = _make_mock_repo(checkpoint)
        config = CheckpointConfig(max_resume_attempts=5)
        strategy = _make_strategy(repo, config=config)

        # Use up one attempt
        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )
        assert result.resume_attempt == 1

        # Finalize clears the counter
        await strategy.finalize(ctx.execution_id)

        # Next recovery starts at 1 again (not 2)
        result2 = await strategy.recover(
            task_execution=task_exec,
            error_message="crash 2",
            context=ctx,
        )
        assert result2.resume_attempt == 1

    async def test_finalize_noop_for_unknown(self) -> None:
        """finalize() on unknown execution_id is a safe no-op."""
        repo = _make_mock_repo()
        strategy = _make_strategy(repo)
        await strategy.finalize("nonexistent-exec")  # Should not raise


@pytest.mark.unit
class TestCheckpointRecoveryFallbackCleanup:
    """Fallback path calls cleanup_checkpoint_artifacts."""

    async def test_no_checkpoint_calls_cleanup(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When no checkpoint exists, cleanup is called before fallback."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        cp_repo = AsyncMock()
        cp_repo.get_latest = AsyncMock(return_value=None)
        cp_repo.delete_by_execution = AsyncMock(return_value=0)
        hb_repo = AsyncMock()
        hb_repo.delete = AsyncMock()

        strategy = CheckpointRecoveryStrategy(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=CheckpointConfig(),
        )

        await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        # Cleanup was called before fallback
        cp_repo.delete_by_execution.assert_awaited_once_with(
            ctx.execution_id,
        )
        hb_repo.delete.assert_awaited_once_with(ctx.execution_id)

    async def test_exhausted_attempts_calls_cleanup(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """After max_resume_attempts, cleanup is called before fallback."""
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        checkpoint = _make_checkpoint(execution_id=ctx.execution_id)
        cp_repo = AsyncMock()
        cp_repo.get_latest = AsyncMock(return_value=checkpoint)
        cp_repo.delete_by_execution = AsyncMock(return_value=1)
        hb_repo = AsyncMock()
        hb_repo.delete = AsyncMock()

        config = CheckpointConfig(max_resume_attempts=0)
        strategy = CheckpointRecoveryStrategy(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
        )

        result = await strategy.recover(
            task_execution=task_exec,
            error_message="crash",
            context=ctx,
        )

        assert result.can_resume is False
        # Cleanup was called
        cp_repo.delete_by_execution.assert_awaited_once()
        hb_repo.delete.assert_awaited_once()


@pytest.mark.unit
class TestCheckpointRecoveryExceptionPropagation:
    """MemoryError and RecursionError propagate through _load_latest."""

    async def test_memory_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        repo = _make_mock_repo(error=MemoryError("out of memory"))
        strategy = _make_strategy(repo)

        with pytest.raises(MemoryError):
            await strategy.recover(
                task_execution=task_exec,
                error_message="crash",
                context=ctx,
            )

    async def test_recursion_error_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx, task_exec = _make_in_progress_ctx(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        repo = _make_mock_repo(error=RecursionError("max depth"))
        strategy = _make_strategy(repo)

        with pytest.raises(RecursionError):
            await strategy.recover(
                task_execution=task_exec,
                error_message="crash",
                context=ctx,
            )
