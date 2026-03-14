"""Tests for task engine request, response, and event models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskData,
    CreateTaskMutation,
    DeleteTaskMutation,
    TaskMutationResult,
    TaskStateChanged,
    TransitionTaskMutation,
    UpdateTaskMutation,
)


@pytest.mark.unit
class TestCreateTaskData:
    """Tests for CreateTaskData model."""

    def test_minimal_construction(self) -> None:
        data = CreateTaskData(
            title="Fix bug",
            description="Fix the login bug",
            type=TaskType.DEVELOPMENT,
            project="proj-1",
            created_by="alice",
        )
        assert data.title == "Fix bug"
        assert data.priority == Priority.MEDIUM
        assert data.estimated_complexity == Complexity.MEDIUM
        assert data.budget_limit == 0.0
        assert data.assigned_to is None

    def test_full_construction(self) -> None:
        data = CreateTaskData(
            title="Implement feature",
            description="Add new dashboard",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-2",
            created_by="bob",
            assigned_to="charlie",
            estimated_complexity=Complexity.COMPLEX,
            budget_limit=5.0,
        )
        assert data.assigned_to == "charlie"
        assert data.budget_limit == 5.0

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be whitespace"):
            CreateTaskData(
                title="  ",
                description="desc",
                type=TaskType.DEVELOPMENT,
                project="proj",
                created_by="alice",
            )

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            CreateTaskData(
                title="Task",
                description="desc",
                type=TaskType.DEVELOPMENT,
                project="proj",
                created_by="alice",
                budget_limit=-1.0,
            )

    def test_title_at_max_length(self) -> None:
        data = CreateTaskData(
            title="x" * 256,
            description="desc",
            type=TaskType.DEVELOPMENT,
            project="proj",
            created_by="alice",
        )
        assert len(data.title) == 256

    def test_title_exceeds_max_length(self) -> None:
        with pytest.raises(ValidationError, match="String should have at most 256"):
            CreateTaskData(
                title="x" * 257,
                description="desc",
                type=TaskType.DEVELOPMENT,
                project="proj",
                created_by="alice",
            )

    def test_description_at_max_length(self) -> None:
        data = CreateTaskData(
            title="Task",
            description="d" * 4096,
            type=TaskType.DEVELOPMENT,
            project="proj",
            created_by="alice",
        )
        assert len(data.description) == 4096

    def test_description_exceeds_max_length(self) -> None:
        with pytest.raises(
            ValidationError,
            match="String should have at most 4096",
        ):
            CreateTaskData(
                title="Task",
                description="d" * 4097,
                type=TaskType.DEVELOPMENT,
                project="proj",
                created_by="alice",
            )

    def test_frozen(self) -> None:
        data = CreateTaskData(
            title="Task",
            description="desc",
            type=TaskType.DEVELOPMENT,
            project="proj",
            created_by="alice",
        )
        with pytest.raises(ValidationError):
            data.title = "changed"  # type: ignore[misc]


@pytest.mark.unit
class TestCreateTaskMutation:
    """Tests for CreateTaskMutation model."""

    def test_construction(self) -> None:
        data = CreateTaskData(
            title="Task",
            description="desc",
            type=TaskType.DEVELOPMENT,
            project="proj",
            created_by="alice",
        )
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=data,
        )
        assert mutation.mutation_type == "create"
        assert mutation.request_id == "req-1"
        assert mutation.requested_by == "alice"

    def test_mutation_type_literal(self) -> None:
        data = CreateTaskData(
            title="Task",
            description="desc",
            type=TaskType.DEVELOPMENT,
            project="proj",
            created_by="alice",
        )
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=data,
        )
        assert mutation.mutation_type == "create"


@pytest.mark.unit
class TestUpdateTaskMutation:
    """Tests for UpdateTaskMutation model."""

    def test_construction(self) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-2",
            requested_by="bob",
            task_id="task-123",
            updates={"title": "New title"},
        )
        assert mutation.mutation_type == "update"
        assert mutation.task_id == "task-123"
        assert mutation.updates == {"title": "New title"}
        assert mutation.expected_version is None

    def test_with_expected_version(self) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-2",
            requested_by="bob",
            task_id="task-123",
            updates={},
            expected_version=3,
        )
        assert mutation.expected_version == 3

    def test_empty_updates(self) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-2",
            requested_by="bob",
            task_id="task-123",
            updates={},
        )
        assert mutation.updates == {}

    def test_expected_version_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            UpdateTaskMutation(
                request_id="req-2",
                requested_by="bob",
                task_id="task-123",
                updates={},
                expected_version=0,
            )


@pytest.mark.unit
class TestTransitionTaskMutation:
    """Tests for TransitionTaskMutation model."""

    def test_construction(self) -> None:
        mutation = TransitionTaskMutation(
            request_id="req-3",
            requested_by="charlie",
            task_id="task-456",
            target_status=TaskStatus.IN_PROGRESS,
            reason="Starting work",
        )
        assert mutation.mutation_type == "transition"
        assert mutation.target_status == TaskStatus.IN_PROGRESS
        assert mutation.reason == "Starting work"
        assert mutation.overrides == {}

    def test_with_overrides(self) -> None:
        mutation = TransitionTaskMutation(
            request_id="req-3",
            requested_by="charlie",
            task_id="task-456",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "dave"},
        )
        assert mutation.overrides == {"assigned_to": "dave"}


@pytest.mark.unit
class TestDeleteTaskMutation:
    """Tests for DeleteTaskMutation model."""

    def test_construction(self) -> None:
        mutation = DeleteTaskMutation(
            request_id="req-4",
            requested_by="alice",
            task_id="task-789",
        )
        assert mutation.mutation_type == "delete"
        assert mutation.task_id == "task-789"


@pytest.mark.unit
class TestCancelTaskMutation:
    """Tests for CancelTaskMutation model."""

    def test_construction(self) -> None:
        mutation = CancelTaskMutation(
            request_id="req-5",
            requested_by="bob",
            task_id="task-abc",
            reason="No longer needed",
        )
        assert mutation.mutation_type == "cancel"
        assert mutation.reason == "No longer needed"


@pytest.mark.unit
class TestTaskMutationResult:
    """Tests for TaskMutationResult model."""

    def test_success_result(self) -> None:
        result = TaskMutationResult(
            request_id="req-1",
            success=True,
            version=1,
        )
        assert result.success is True
        assert result.task is None
        assert result.error is None

    def test_failure_result(self) -> None:
        result = TaskMutationResult(
            request_id="req-1",
            success=False,
            error="Not found",
            error_code="not_found",
        )
        assert result.success is False
        assert result.error == "Not found"
        assert result.error_code == "not_found"
        assert result.version == 0

    def test_frozen(self) -> None:
        result = TaskMutationResult(
            request_id="req-1",
            success=True,
            version=1,
        )
        with pytest.raises(ValidationError):
            result.success = False  # type: ignore[misc]


@pytest.mark.unit
class TestTaskStateChanged:
    """Tests for TaskStateChanged event model."""

    def test_construction(self) -> None:
        event = TaskStateChanged(
            mutation_type="create",
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            new_status=TaskStatus.CREATED,
            version=1,
        )
        assert event.mutation_type == "create"
        assert event.previous_status is None
        assert event.new_status == TaskStatus.CREATED
        assert event.timestamp is not None

    def test_transition_event(self) -> None:
        event = TaskStateChanged(
            mutation_type="transition",
            request_id="req-2",
            requested_by="bob",
            task_id="task-2",
            previous_status=TaskStatus.CREATED,
            new_status=TaskStatus.ASSIGNED,
            version=2,
        )
        assert event.previous_status == TaskStatus.CREATED
        assert event.new_status == TaskStatus.ASSIGNED

    def test_delete_event(self) -> None:
        event = TaskStateChanged(
            mutation_type="delete",
            request_id="req-3",
            requested_by="charlie",
            task_id="task-3",
            version=0,
        )
        assert event.task is None
        assert event.previous_status is None
        assert event.new_status is None

    def test_reason_field_populated(self) -> None:
        event = TaskStateChanged(
            mutation_type="transition",
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            previous_status=TaskStatus.ASSIGNED,
            new_status=TaskStatus.IN_PROGRESS,
            version=2,
            reason="Starting work on task",
        )
        assert event.reason == "Starting work on task"

    def test_reason_field_default_none(self) -> None:
        event = TaskStateChanged(
            mutation_type="create",
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            new_status=TaskStatus.CREATED,
            version=1,
        )
        assert event.reason is None

    def test_cancel_event_has_reason(self) -> None:
        event = TaskStateChanged(
            mutation_type="cancel",
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            previous_status=TaskStatus.ASSIGNED,
            new_status=TaskStatus.CANCELLED,
            version=3,
            reason="No longer needed",
        )
        assert event.reason == "No longer needed"

    def test_serialization_roundtrip(self) -> None:
        event = TaskStateChanged(
            mutation_type="create",
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            new_status=TaskStatus.CREATED,
            version=1,
        )
        json_str = event.model_dump_json()
        restored = TaskStateChanged.model_validate_json(json_str)
        assert restored.mutation_type == event.mutation_type
        assert restored.request_id == event.request_id
        assert restored.version == event.version


@pytest.mark.unit
class TestDeepCopyIsolation:
    """Verify deep-copy isolation of mutable dict fields."""

    def test_update_mutation_isolates_updates(self) -> None:
        """Mutating the original dict after construction has no effect."""
        original = {"title": "Original"}
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            updates=original,
        )
        original["title"] = "Tampered"
        assert mutation.updates["title"] == "Original"

    def test_transition_mutation_isolates_overrides(self) -> None:
        """Mutating the original dict after construction has no effect."""
        original: dict[str, object] = {"assigned_to": "bob"}
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides=original,
        )
        original["assigned_to"] = "tampered"
        assert mutation.overrides["assigned_to"] == "bob"

    def test_update_mutation_nested_dict_isolation(self) -> None:
        """Nested mutable values are also deep-copied."""
        nested = {"key": "value"}
        original: dict[str, object] = {"description": nested}
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            updates=original,
        )
        nested["key"] = "tampered"
        inner = mutation.updates["description"]
        assert isinstance(inner, dict)
        assert inner["key"] == "value"


@pytest.mark.unit
class TestUnknownFieldRejection:
    """Verify unknown field names are rejected in updates/overrides."""

    def test_update_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Unknown task fields"):
            UpdateTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_id="task-1",
                updates={"nonexistent_field": "value"},
            )

    def test_update_rejects_multiple_unknown_fields(self) -> None:
        with pytest.raises(ValidationError, match="Unknown task fields"):
            UpdateTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_id="task-1",
                updates={"foo": 1, "bar": 2},
            )

    def test_transition_rejects_unknown_override(self) -> None:
        with pytest.raises(ValidationError, match="Unknown task fields"):
            TransitionTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_id="task-1",
                target_status=TaskStatus.ASSIGNED,
                reason="test",
                overrides={"nonexistent_field": "value"},
            )
