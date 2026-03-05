"""Tests for TaskExecution and StatusTransition models."""

from datetime import UTC, datetime

import pytest
import structlog.testing
from pydantic import ValidationError

from ai_company.core.enums import TaskStatus
from ai_company.core.task import Task
from ai_company.engine.task_execution import (
    StatusTransition,
    TaskExecution,
    _add_token_usage,
)
from ai_company.observability.events import (
    EXECUTION_COST_RECORDED,
    EXECUTION_TASK_CREATED,
    EXECUTION_TASK_TRANSITION,
)
from ai_company.providers.models import TokenUsage


class TestStatusTransition:
    """StatusTransition construction and immutability."""

    def test_construction(self) -> None:
        now = datetime.now(UTC)
        t = StatusTransition(
            from_status=TaskStatus.ASSIGNED,
            to_status=TaskStatus.IN_PROGRESS,
            timestamp=now,
            reason="starting work",
        )
        assert t.from_status is TaskStatus.ASSIGNED
        assert t.to_status is TaskStatus.IN_PROGRESS
        assert t.timestamp == now
        assert t.reason == "starting work"

    def test_default_reason(self) -> None:
        t = StatusTransition(
            from_status=TaskStatus.ASSIGNED,
            to_status=TaskStatus.IN_PROGRESS,
            timestamp=datetime.now(UTC),
        )
        assert t.reason == ""

    def test_frozen(self) -> None:
        t = StatusTransition(
            from_status=TaskStatus.ASSIGNED,
            to_status=TaskStatus.IN_PROGRESS,
            timestamp=datetime.now(UTC),
        )
        with pytest.raises(ValidationError, match="frozen"):
            t.from_status = TaskStatus.CREATED  # type: ignore[misc]


class TestTaskExecutionFromTask:
    """TaskExecution.from_task factory."""

    def test_status_matches_task(self, sample_task_with_criteria: Task) -> None:
        exe = TaskExecution.from_task(sample_task_with_criteria)
        assert exe.status is sample_task_with_criteria.status
        assert exe.task is sample_task_with_criteria

    def test_defaults(self, sample_task_with_criteria: Task) -> None:
        exe = TaskExecution.from_task(sample_task_with_criteria)
        assert exe.transition_log == ()
        assert exe.accumulated_cost.cost_usd == 0.0
        assert exe.accumulated_cost.input_tokens == 0
        assert exe.turn_count == 0
        assert exe.started_at is None
        assert exe.completed_at is None

    def test_not_terminal_initially(self, sample_task_with_criteria: Task) -> None:
        exe = TaskExecution.from_task(sample_task_with_criteria)
        assert exe.is_terminal is False


class TestTaskExecutionTransitions:
    """TaskExecution.with_transition valid and invalid paths."""

    def test_valid_transition(self, sample_task_execution: TaskExecution) -> None:
        # ASSIGNED -> IN_PROGRESS
        result = sample_task_execution.with_transition(
            TaskStatus.IN_PROGRESS, reason="begin"
        )
        assert result.status is TaskStatus.IN_PROGRESS
        assert len(result.transition_log) == 1
        assert result.transition_log[0].from_status is TaskStatus.ASSIGNED
        assert result.transition_log[0].to_status is TaskStatus.IN_PROGRESS
        assert result.transition_log[0].reason == "begin"

    def test_invalid_transition_raises(
        self, sample_task_execution: TaskExecution
    ) -> None:
        # ASSIGNED -> COMPLETED is not valid
        with pytest.raises(ValueError, match="Invalid task status"):
            sample_task_execution.with_transition(TaskStatus.COMPLETED)

    def test_transition_log_accumulates(
        self, sample_task_execution: TaskExecution
    ) -> None:
        step1 = sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        step2 = step1.with_transition(TaskStatus.IN_REVIEW)
        assert len(step2.transition_log) == 2
        assert step2.transition_log[0].to_status is TaskStatus.IN_PROGRESS
        assert step2.transition_log[1].to_status is TaskStatus.IN_REVIEW

    def test_started_at_set_on_in_progress(
        self, sample_task_execution: TaskExecution
    ) -> None:
        before = datetime.now(UTC)
        result = sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        assert result.started_at is not None
        assert result.started_at >= before

    def test_started_at_not_overwritten_on_rework(
        self, sample_task_execution: TaskExecution
    ) -> None:
        step1 = sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        original_started = step1.started_at
        step2 = step1.with_transition(TaskStatus.IN_REVIEW)
        step3 = step2.with_transition(TaskStatus.IN_PROGRESS)
        assert step3.started_at == original_started

    def test_completed_at_set_on_completed(
        self, sample_task_execution: TaskExecution
    ) -> None:
        step1 = sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        step2 = step1.with_transition(TaskStatus.IN_REVIEW)
        before = datetime.now(UTC)
        step3 = step2.with_transition(TaskStatus.COMPLETED)
        assert step3.completed_at is not None
        assert step3.completed_at >= before
        assert step3.is_terminal is True

    def test_completed_at_set_on_cancelled(
        self, sample_task_execution: TaskExecution
    ) -> None:
        result = sample_task_execution.with_transition(TaskStatus.CANCELLED)
        assert result.completed_at is not None
        assert result.is_terminal is True

    def test_full_lifecycle(self, sample_task_execution: TaskExecution) -> None:
        exe = sample_task_execution
        exe = exe.with_transition(TaskStatus.IN_PROGRESS)
        exe = exe.with_transition(TaskStatus.IN_REVIEW)
        exe = exe.with_transition(TaskStatus.COMPLETED, reason="lgtm")
        assert exe.status is TaskStatus.COMPLETED
        assert len(exe.transition_log) == 3
        assert exe.is_terminal is True


class TestTaskExecutionCost:
    """TaskExecution.with_cost accumulation."""

    def test_accumulates_cost(
        self,
        sample_task_execution: TaskExecution,
        sample_token_usage: TokenUsage,
    ) -> None:
        result = sample_task_execution.with_cost(sample_token_usage)
        assert result.accumulated_cost.input_tokens == 100
        assert result.accumulated_cost.output_tokens == 50
        assert result.accumulated_cost.total_tokens == 150
        assert result.accumulated_cost.cost_usd == pytest.approx(0.01)
        assert result.turn_count == 1

    def test_multiple_accumulations(
        self,
        sample_task_execution: TaskExecution,
        sample_token_usage: TokenUsage,
    ) -> None:
        step1 = sample_task_execution.with_cost(sample_token_usage)
        step2 = step1.with_cost(sample_token_usage)
        assert step2.accumulated_cost.input_tokens == 200
        assert step2.accumulated_cost.output_tokens == 100
        assert step2.accumulated_cost.total_tokens == 300
        assert step2.accumulated_cost.cost_usd == pytest.approx(0.02)
        assert step2.turn_count == 2


class TestTaskExecutionSnapshot:
    """TaskExecution.to_task_snapshot."""

    def test_snapshot_has_updated_status(
        self, sample_task_execution: TaskExecution
    ) -> None:
        step1 = sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        snapshot = step1.to_task_snapshot()
        assert isinstance(snapshot, Task)
        assert snapshot.status is TaskStatus.IN_PROGRESS

    def test_snapshot_preserves_task_fields(
        self, sample_task_execution: TaskExecution
    ) -> None:
        snapshot = sample_task_execution.to_task_snapshot()
        assert snapshot.id == sample_task_execution.task.id
        assert snapshot.title == sample_task_execution.task.title
        assert snapshot.project == sample_task_execution.task.project


class TestTaskExecutionImmutability:
    """TaskExecution is frozen and model_copy preserves originals."""

    def test_frozen(self, sample_task_execution: TaskExecution) -> None:
        with pytest.raises(ValidationError, match="frozen"):
            sample_task_execution.status = TaskStatus.IN_PROGRESS  # type: ignore[misc]

    def test_original_unchanged_after_transition(
        self, sample_task_execution: TaskExecution
    ) -> None:
        original_status = sample_task_execution.status
        _ = sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        assert sample_task_execution.status is original_status
        assert sample_task_execution.transition_log == ()

    def test_original_unchanged_after_cost(
        self,
        sample_task_execution: TaskExecution,
        sample_token_usage: TokenUsage,
    ) -> None:
        _ = sample_task_execution.with_cost(sample_token_usage)
        assert sample_task_execution.turn_count == 0
        assert sample_task_execution.accumulated_cost.cost_usd == 0.0


class TestAddTokenUsage:
    """Helper _add_token_usage."""

    def test_sums_correctly(self) -> None:
        a = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            cost_usd=0.01,
        )
        b = TokenUsage(
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            cost_usd=0.02,
        )
        result = _add_token_usage(a, b)
        assert result.input_tokens == 30
        assert result.output_tokens == 15
        assert result.total_tokens == 45
        assert result.cost_usd == pytest.approx(0.03)

    def test_maintains_total_invariant(self) -> None:
        a = TokenUsage(
            input_tokens=7,
            output_tokens=3,
            total_tokens=10,
            cost_usd=0.0,
        )
        b = TokenUsage(
            input_tokens=13,
            output_tokens=7,
            total_tokens=20,
            cost_usd=0.0,
        )
        result = _add_token_usage(a, b)
        assert result.total_tokens == result.input_tokens + result.output_tokens


class TestTaskExecutionLogging:
    """Event constants are logged on transitions."""

    def test_from_task_logs_created(self, sample_task_with_criteria: Task) -> None:
        with structlog.testing.capture_logs() as logs:
            TaskExecution.from_task(sample_task_with_criteria)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_TASK_CREATED in events

    def test_with_transition_logs_event(
        self, sample_task_execution: TaskExecution
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            sample_task_execution.with_transition(TaskStatus.IN_PROGRESS)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_TASK_TRANSITION in events

    def test_with_cost_logs_event(
        self,
        sample_task_execution: TaskExecution,
        sample_token_usage: TokenUsage,
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            sample_task_execution.with_cost(sample_token_usage)
        events = [entry["event"] for entry in logs]
        assert EXECUTION_COST_RECORDED in events
