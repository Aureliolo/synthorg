"""Tests for crash recovery strategy protocol and FailAndReassignStrategy."""

from typing import TYPE_CHECKING

import pytest
import structlog.testing

from synthorg.core.enums import TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

from pydantic import ValidationError

from synthorg.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
    RecoveryStrategy,
)
from synthorg.observability.events.execution import (
    EXECUTION_RECOVERY_COMPLETE,
    EXECUTION_RECOVERY_SNAPSHOT,
    EXECUTION_RECOVERY_START,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestRecoveryStrategyProtocol:
    """FailAndReassignStrategy satisfies the RecoveryStrategy protocol."""

    def test_is_runtime_checkable(self) -> None:
        strategy = FailAndReassignStrategy()
        assert isinstance(strategy, RecoveryStrategy)

    def test_get_strategy_type(self) -> None:
        strategy = FailAndReassignStrategy()
        assert strategy.get_strategy_type() == "fail_reassign"


@pytest.mark.unit
class TestFailAndReassignStrategy:
    """FailAndReassignStrategy behavior."""

    async def test_happy_path_transitions_to_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Task transitions to FAILED, can_reassign=True when retries remain."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=ctx.task_execution,
            error_message="LLM crashed",
            context=ctx,
        )

        assert isinstance(result, RecoveryResult)
        assert result.task_execution.status is TaskStatus.FAILED
        assert result.strategy_type == "fail_reassign"
        assert result.can_reassign is True
        assert result.error_message == "LLM crashed"

    async def test_max_retries_exceeded_cannot_reassign(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """can_reassign=False when retry_count >= max_retries."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        # Simulate retry_count already at max_retries (default=1)
        exe_with_retries = ctx.task_execution.model_copy(
            update={"retry_count": 1},
        )

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=exe_with_retries,
            error_message="Failed again",
            context=ctx,
        )

        assert result.can_reassign is False
        assert result.task_execution.status is TaskStatus.FAILED

    async def test_zero_max_retries_never_reassignable(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Task with max_retries=0 is never reassignable."""
        task = Task(
            id="task-no-retries",
            title="No retries allowed",
            description="This task cannot be retried.",
            type=TaskType.DEVELOPMENT,
            project="proj-001",
            created_by="manager",
            assigned_to=str(sample_agent_with_personality.id),
            status=TaskStatus.ASSIGNED,
            max_retries=0,
        )
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=task,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=ctx.task_execution,
            error_message="Crashed",
            context=ctx,
        )

        assert result.can_reassign is False
        assert result.task_execution.status is TaskStatus.FAILED

    async def test_snapshot_is_redacted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Snapshot contains metadata but no message contents."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=ctx.task_execution,
            error_message="Provider error",
            context=ctx,
        )

        snapshot = result.context_snapshot
        assert snapshot.task_id == sample_task_with_criteria.id
        assert snapshot.agent_id == str(sample_agent_with_personality.id)
        assert snapshot.turn_count >= 0
        # Snapshot is an AgentContextSnapshot — has no message contents
        assert not hasattr(snapshot, "conversation")

    async def test_error_message_captured(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Error message is preserved in the result."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=ctx.task_execution,
            error_message="Specific error: connection reset",
            context=ctx,
        )

        assert result.error_message == "Specific error: connection reset"

    async def test_recovery_result_frozen(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """RecoveryResult fields cannot be reassigned (frozen model)."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        result = await strategy.recover(
            task_execution=ctx.task_execution,
            error_message="Crashed",
            context=ctx,
        )

        with pytest.raises(ValidationError, match="frozen"):
            result.error_message = "changed"  # type: ignore[misc]

    async def test_recovery_logs_events(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Recovery emits start, snapshot, and complete events."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        ctx = ctx.with_task_transition(
            TaskStatus.IN_PROGRESS,
            reason="starting",
        )
        assert ctx.task_execution is not None

        strategy = FailAndReassignStrategy()
        with structlog.testing.capture_logs() as logs:
            await strategy.recover(
                task_execution=ctx.task_execution,
                error_message="Error",
                context=ctx,
            )

        events = [entry["event"] for entry in logs]
        assert EXECUTION_RECOVERY_START in events
        assert EXECUTION_RECOVERY_SNAPSHOT in events
        assert EXECUTION_RECOVERY_COMPLETE in events
