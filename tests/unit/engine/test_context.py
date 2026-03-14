"""Tests for AgentContext and AgentContextSnapshot models."""

from datetime import UTC, datetime

import pytest
import structlog.testing
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.context import (
    DEFAULT_MAX_TURNS,
    AgentContext,
    AgentContextSnapshot,
)
from synthorg.engine.errors import ExecutionStateError, MaxTurnsExceededError
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_CREATED,
    EXECUTION_CONTEXT_NO_TASK,
    EXECUTION_CONTEXT_SNAPSHOT,
    EXECUTION_CONTEXT_TRANSITION_FAILED,
    EXECUTION_CONTEXT_TURN,
    EXECUTION_MAX_TURNS_EXCEEDED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, TokenUsage


def _make_assistant_msg(text: str = "hello") -> ChatMessage:
    """Create a simple assistant message for testing."""
    return ChatMessage(role=MessageRole.ASSISTANT, content=text)


def _make_user_msg(text: str = "hi") -> ChatMessage:
    """Create a simple user message for testing."""
    return ChatMessage(role=MessageRole.USER, content=text)


@pytest.mark.unit
class TestAgentContextFromIdentity:
    """AgentContext.from_identity factory."""

    def test_with_task(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert ctx.identity is sample_agent_with_personality
        assert ctx.task_execution is not None
        assert ctx.task_execution.task is sample_task_with_criteria

    def test_without_task(self, sample_agent_with_personality: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        assert ctx.task_execution is None

    def test_defaults(self, sample_agent_with_personality: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        assert ctx.conversation == ()
        assert ctx.accumulated_cost.cost_usd == 0.0
        assert ctx.turn_count == 0
        assert ctx.max_turns == DEFAULT_MAX_TURNS
        assert ctx.has_turns_remaining is True

    def test_execution_id_generated(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx1 = AgentContext.from_identity(sample_agent_with_personality)
        ctx2 = AgentContext.from_identity(sample_agent_with_personality)
        assert ctx1.execution_id != ctx2.execution_id
        assert len(ctx1.execution_id) > 0

    def test_custom_max_turns(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality, max_turns=5)
        assert ctx.max_turns == 5

    def test_started_at_set(self, sample_agent_with_personality: AgentIdentity) -> None:
        before = datetime.now(UTC)
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        assert ctx.started_at >= before


@pytest.mark.unit
class TestAgentContextConversation:
    """AgentContext.with_message."""

    def test_appends_message(self, sample_agent_context: AgentContext) -> None:
        msg = _make_user_msg()
        result = sample_agent_context.with_message(msg)
        assert len(result.conversation) == 1
        assert result.conversation[0] is msg

    def test_original_unchanged(self, sample_agent_context: AgentContext) -> None:
        msg = _make_user_msg()
        _ = sample_agent_context.with_message(msg)
        assert sample_agent_context.conversation == ()

    def test_multiple_messages(self, sample_agent_context: AgentContext) -> None:
        msg1 = _make_user_msg("first")
        msg2 = _make_assistant_msg("second")
        step1 = sample_agent_context.with_message(msg1)
        step2 = step1.with_message(msg2)
        assert len(step2.conversation) == 2
        assert step2.conversation[0].content == "first"
        assert step2.conversation[1].content == "second"


@pytest.mark.unit
class TestAgentContextTurns:
    """AgentContext.with_turn_completed and has_turns_remaining."""

    def test_increments_turn_and_cost(
        self,
        sample_agent_context: AgentContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        msg = _make_assistant_msg("response")
        result = sample_agent_context.with_turn_completed(sample_token_usage, msg)
        assert result.turn_count == 1
        assert result.accumulated_cost.input_tokens == 100
        assert result.accumulated_cost.cost_usd == pytest.approx(0.01)
        assert len(result.conversation) == 1
        assert result.conversation[0] is msg

    def test_accumulates_on_task_execution(
        self,
        sample_agent_context: AgentContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        msg = _make_assistant_msg()
        result = sample_agent_context.with_turn_completed(sample_token_usage, msg)
        assert result.task_execution is not None
        assert result.task_execution.turn_count == 1
        assert result.task_execution.accumulated_cost.cost_usd == pytest.approx(0.01)

    def test_no_task_execution_still_works(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_token_usage: TokenUsage,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        msg = _make_assistant_msg()
        result = ctx.with_turn_completed(sample_token_usage, msg)
        assert result.turn_count == 1
        assert result.task_execution is None

    def test_has_turns_remaining_boundary(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality, max_turns=2)
        usage = TokenUsage(
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )
        msg = _make_assistant_msg()
        assert ctx.has_turns_remaining is True
        step1 = ctx.with_turn_completed(usage, msg)
        assert step1.has_turns_remaining is True
        step2 = step1.with_turn_completed(usage, msg)
        assert step2.has_turns_remaining is False

    def test_max_turns_exceeded_raises(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality, max_turns=1)
        usage = TokenUsage(
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )
        msg = _make_assistant_msg()
        step1 = ctx.with_turn_completed(usage, msg)
        with pytest.raises(MaxTurnsExceededError, match="max_turns"):
            step1.with_turn_completed(usage, msg)

    def test_max_turns_zero_rejected(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        with pytest.raises(ValidationError):
            AgentContext.from_identity(sample_agent_with_personality, max_turns=0)


@pytest.mark.unit
class TestAgentContextTransitions:
    """AgentContext.with_task_transition."""

    def test_delegates_to_task_execution(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="go"
        )
        assert result.task_execution is not None
        assert result.task_execution.status is TaskStatus.IN_PROGRESS

    def test_raises_without_task_execution(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        with pytest.raises(ExecutionStateError, match="no task execution"):
            ctx.with_task_transition(TaskStatus.IN_PROGRESS)

    def test_invalid_transition_raises_value_error(
        self, sample_agent_context: AgentContext
    ) -> None:
        with pytest.raises(ValueError, match="Invalid task status"):
            sample_agent_context.with_task_transition(TaskStatus.COMPLETED)


@pytest.mark.unit
class TestAgentContextSnapshot:
    """AgentContext.to_snapshot."""

    def test_produces_correct_snapshot(
        self, sample_agent_context: AgentContext
    ) -> None:
        snapshot = sample_agent_context.to_snapshot()
        assert isinstance(snapshot, AgentContextSnapshot)
        assert snapshot.execution_id == sample_agent_context.execution_id
        assert snapshot.agent_id == str(sample_agent_context.identity.id)
        assert snapshot.turn_count == 0
        assert snapshot.message_count == 0
        assert snapshot.accumulated_cost.cost_usd == 0.0

    def test_snapshot_with_task(self, sample_agent_context: AgentContext) -> None:
        assert sample_agent_context.task_execution is not None
        snapshot = sample_agent_context.to_snapshot()
        assert snapshot.task_id == sample_agent_context.task_execution.task.id
        assert snapshot.task_status == sample_agent_context.task_execution.status

    def test_snapshot_without_task(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        snapshot = ctx.to_snapshot()
        assert snapshot.task_id is None
        assert snapshot.task_status is None

    def test_snapshot_is_frozen(self, sample_agent_context: AgentContext) -> None:
        snapshot = sample_agent_context.to_snapshot()
        with pytest.raises(ValidationError, match="frozen"):
            snapshot.turn_count = 99  # type: ignore[misc]

    def test_snapshot_timestamps(self, sample_agent_context: AgentContext) -> None:
        before = datetime.now(UTC)
        snapshot = sample_agent_context.to_snapshot()
        assert snapshot.started_at == sample_agent_context.started_at
        assert snapshot.snapshot_at >= before

    def test_snapshot_task_id_without_status_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id and task_status"):
            AgentContextSnapshot(
                execution_id="exec-1",
                agent_id="agent-1",
                task_id="task-1",
                task_status=None,
                turn_count=0,
                accumulated_cost=TokenUsage(
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                ),
                started_at=datetime.now(UTC),
                snapshot_at=datetime.now(UTC),
                message_count=0,
            )

    def test_snapshot_task_status_without_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id and task_status"):
            AgentContextSnapshot(
                execution_id="exec-1",
                agent_id="agent-1",
                task_id=None,
                task_status=TaskStatus.IN_PROGRESS,
                turn_count=0,
                accumulated_cost=TokenUsage(
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                ),
                started_at=datetime.now(UTC),
                snapshot_at=datetime.now(UTC),
                message_count=0,
            )


@pytest.mark.unit
class TestAgentContextImmutability:
    """AgentContext is frozen and model_copy preserves originals."""

    def test_frozen(self, sample_agent_context: AgentContext) -> None:
        with pytest.raises(ValidationError, match="frozen"):
            sample_agent_context.turn_count = 99  # type: ignore[misc]

    def test_original_unchanged_after_turn(
        self,
        sample_agent_context: AgentContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        msg = _make_assistant_msg()
        _ = sample_agent_context.with_turn_completed(sample_token_usage, msg)
        assert sample_agent_context.turn_count == 0
        assert sample_agent_context.conversation == ()

    def test_original_unchanged_after_transition(
        self, sample_agent_context: AgentContext
    ) -> None:
        assert sample_agent_context.task_execution is not None
        original_status = sample_agent_context.task_execution.status
        _ = sample_agent_context.with_task_transition(TaskStatus.IN_PROGRESS)
        assert sample_agent_context.task_execution.status is original_status


@pytest.mark.unit
class TestAgentContextLogging:
    """Event constants are logged."""

    def test_from_identity_logs_created(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            AgentContext.from_identity(sample_agent_with_personality)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_CONTEXT_CREATED in events

    def test_with_turn_completed_logs_event(
        self,
        sample_agent_context: AgentContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        msg = _make_assistant_msg()
        with structlog.testing.capture_logs() as logs:
            sample_agent_context.with_turn_completed(sample_token_usage, msg)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_CONTEXT_TURN in events

    def test_to_snapshot_logs_event(self, sample_agent_context: AgentContext) -> None:
        with structlog.testing.capture_logs() as logs:
            sample_agent_context.to_snapshot()
        events = [entry["event"] for entry in logs]
        assert EXECUTION_CONTEXT_SNAPSHOT in events

    def test_no_task_transition_logs_error(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ExecutionStateError),
        ):
            ctx.with_task_transition(TaskStatus.IN_PROGRESS)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_CONTEXT_NO_TASK in events

    def test_max_turns_exceeded_logs_error(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality, max_turns=1)
        usage = TokenUsage(
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )
        msg = _make_assistant_msg()
        step1 = ctx.with_turn_completed(usage, msg)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(MaxTurnsExceededError),
        ):
            step1.with_turn_completed(usage, msg)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_MAX_TURNS_EXCEEDED in events

    def test_invalid_transition_logs_warning(
        self, sample_agent_context: AgentContext
    ) -> None:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValueError, match="Invalid task status"),
        ):
            sample_agent_context.with_task_transition(TaskStatus.COMPLETED)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_CONTEXT_TRANSITION_FAILED in events
