"""Tests for RecoveryResult checkpoint-related fields (can_resume, resume_attempt)."""

from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.context import AgentContext
from synthorg.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanResumeField:
    """RecoveryResult.can_resume computed field."""

    def test_can_resume_true_when_checkpoint_context_set(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """can_resume is True when checkpoint_context_json is provided."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
        assert ctx.task_execution is not None

        result = RecoveryResult(
            task_execution=ctx.task_execution,
            strategy_type="checkpoint",
            context_snapshot=ctx.to_snapshot(),
            error_message="crash",
            checkpoint_context_json='{"state": "partial"}',
            resume_attempt=1,
        )

        assert result.can_resume is True

    def test_can_resume_false_when_checkpoint_context_none(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """can_resume is False when checkpoint_context_json is None (default)."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
        assert ctx.task_execution is not None

        result = RecoveryResult(
            task_execution=ctx.task_execution.with_transition(
                TaskStatus.FAILED, reason="crash"
            ),
            strategy_type="fail_reassign",
            context_snapshot=ctx.to_snapshot(),
            error_message="crash",
        )

        assert result.can_resume is False
        assert result.checkpoint_context_json is None


@pytest.mark.unit
class TestCheckpointConsistencyValidator:
    """RecoveryResult rejects inconsistent checkpoint_context_json / resume_attempt."""

    def test_json_set_but_attempt_zero_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Setting checkpoint_context_json without resume_attempt > 0 raises."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
        assert ctx.task_execution is not None

        with pytest.raises(ValueError, match="must be consistent"):
            RecoveryResult(
                task_execution=ctx.task_execution,
                strategy_type="checkpoint",
                context_snapshot=ctx.to_snapshot(),
                error_message="crash",
                checkpoint_context_json='{"state": "partial"}',
            )

    def test_attempt_set_but_json_none_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Setting resume_attempt > 0 without checkpoint_context_json raises."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
        assert ctx.task_execution is not None

        with pytest.raises(ValueError, match="must be consistent"):
            RecoveryResult(
                task_execution=ctx.task_execution,
                strategy_type="checkpoint",
                context_snapshot=ctx.to_snapshot(),
                error_message="crash",
                resume_attempt=1,
            )


@pytest.mark.unit
class TestResumeAttemptDefault:
    """RecoveryResult.resume_attempt defaults to 0."""

    def test_defaults_to_zero(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
        assert ctx.task_execution is not None

        result = RecoveryResult(
            task_execution=ctx.task_execution.with_transition(
                TaskStatus.FAILED, reason="crash"
            ),
            strategy_type="fail_reassign",
            context_snapshot=ctx.to_snapshot(),
            error_message="crash",
        )

        assert result.resume_attempt == 0


@pytest.mark.unit
class TestBackwardCompatibility:
    """Existing FailAndReassignStrategy produces can_resume=False."""

    async def test_fail_and_reassign_has_no_resume(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """FailAndReassignStrategy result has can_resume=False and resume_attempt=0."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="starting")
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=ctx.task_execution,
            error_message="LLM crashed",
            context=ctx,
        )

        assert result.can_resume is False
        assert result.checkpoint_context_json is None
        assert result.resume_attempt == 0
        assert result.strategy_type == "fail_reassign"


@pytest.mark.unit
class TestFailAndReassignFinalize:
    """FailAndReassignStrategy.finalize() is a no-op."""

    async def test_finalize_is_noop(self) -> None:
        strategy = FailAndReassignStrategy()
        # Should not raise — no state to clean up
        await strategy.finalize("exec-001")

    async def test_finalize_idempotent(self) -> None:
        strategy = FailAndReassignStrategy()
        await strategy.finalize("exec-001")
        await strategy.finalize("exec-001")  # Calling twice is safe


@pytest.mark.unit
class TestRecoveryResultCheckpointJsonValidation:
    """RecoveryResult rejects invalid checkpoint_context_json values."""

    def test_invalid_json_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Invalid JSON in checkpoint_context_json raises ValueError."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        with pytest.raises(ValueError, match="valid JSON"):
            RecoveryResult(
                task_execution=ctx.task_execution,
                strategy_type="checkpoint",
                context_snapshot=ctx.to_snapshot(),
                error_message="crash",
                checkpoint_context_json="{not valid}",
                resume_attempt=1,
            )

    def test_json_array_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """JSON array in checkpoint_context_json raises ValueError."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        with pytest.raises(ValueError, match="JSON object"):
            RecoveryResult(
                task_execution=ctx.task_execution,
                strategy_type="checkpoint",
                context_snapshot=ctx.to_snapshot(),
                error_message="crash",
                checkpoint_context_json="[1, 2, 3]",
                resume_attempt=1,
            )

    def test_json_primitive_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """JSON primitive in checkpoint_context_json raises ValueError."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        with pytest.raises(ValueError, match="JSON object"):
            RecoveryResult(
                task_execution=ctx.task_execution,
                strategy_type="checkpoint",
                context_snapshot=ctx.to_snapshot(),
                error_message="crash",
                checkpoint_context_json='"just a string"',
                resume_attempt=1,
            )
