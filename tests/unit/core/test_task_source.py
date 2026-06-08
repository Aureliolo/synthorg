"""Unit tests for TaskSource enum and Task.source field."""

import pytest
from pydantic import ValidationError

from synthorg.communication.delegation.service import DelegationService
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskSource, TaskStatus, TaskType
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


class TestTaskSourceEnum:
    """Tests for the TaskSource enum."""

    def test_has_three_members(self) -> None:
        assert len(TaskSource) == 3

    def test_values(self) -> None:
        assert TaskSource.INTERNAL.value == "internal"
        assert TaskSource.CLIENT.value == "client"
        assert TaskSource.SIMULATION.value == "simulation"


class TestTaskSourceField:
    """Tests for the Task.source field."""

    def _make_task(self, **overrides: object) -> Task:
        defaults: dict[str, object] = {
            "id": as_uuid("task-001"),
            "title": "Test task",
            "description": "A test task",
            "type": TaskType.DEVELOPMENT,
            "project": "proj-001",
            "created_by": "manager",
        }
        defaults.update(overrides)
        return Task(**defaults)  # type: ignore[arg-type]

    def test_source_defaults_to_none(self) -> None:
        task = self._make_task()
        assert task.source is None

    @pytest.mark.parametrize("source", list(TaskSource))
    def test_source_values(self, source: TaskSource) -> None:
        task = self._make_task(source=source)
        assert task.source == source

    def test_source_preserved_in_transition(self) -> None:
        task = self._make_task(source=TaskSource.CLIENT)
        assigned = task.with_transition(
            TaskStatus.ASSIGNED,
            assigned_to="agent-1",
        )
        assert assigned.source == TaskSource.CLIENT

    def test_source_preserved_through_full_lifecycle(self) -> None:
        task = self._make_task(source=TaskSource.CLIENT)
        assigned = task.with_transition(TaskStatus.ASSIGNED, assigned_to="agent-1")
        in_progress = assigned.with_transition(TaskStatus.IN_PROGRESS)
        in_review = in_progress.with_transition(TaskStatus.IN_REVIEW)
        completed = in_review.with_transition(TaskStatus.COMPLETED)
        assert completed.source == TaskSource.CLIENT

    def test_invalid_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make_task(source="invalid")

    def test_auth_required_requires_assignee(self) -> None:
        task = self._make_task()
        assigned = task.with_transition(TaskStatus.ASSIGNED, assigned_to="agent-1")
        auth = assigned.with_transition(TaskStatus.AUTH_REQUIRED)
        assert auth.assigned_to == "agent-1"

    def test_auth_required_without_assignee_rejected(self) -> None:
        with pytest.raises(ValidationError, match="assigned_to is required"):
            Task(
                id=as_uuid("task-auth"),
                title="Auth task",
                description="Needs auth",
                type=TaskType.DEVELOPMENT,
                project="proj-001",
                created_by="manager",
                status=TaskStatus.AUTH_REQUIRED,
            )

    def test_rejected_allows_no_assignee(self) -> None:
        task = self._make_task()
        rejected = task.with_transition(TaskStatus.REJECTED)
        assert rejected.assigned_to is None

    def test_reject_delegated_task(self) -> None:
        task = self._make_task()
        rejected = DelegationService.reject_delegated_task(task)
        assert rejected.status == TaskStatus.REJECTED
        assert rejected.assigned_to is None
