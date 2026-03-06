"""Tests for the Task and AcceptanceCriterion domain models."""

import pytest
import structlog
from pydantic import ValidationError

from ai_company.core.artifact import ExpectedArtifact
from ai_company.core.enums import (
    ArtifactType,
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from ai_company.core.task import AcceptanceCriterion, Task
from ai_company.observability.events import TASK_STATUS_CHANGED

pytestmark = pytest.mark.timeout(30)

# ── Helpers ──────────────────────────────────────────────────────

_TASK_KWARGS: dict[str, object] = {
    "id": "task-123",
    "title": "Implement user authentication",
    "description": "Create REST endpoints for login, register, logout",
    "type": TaskType.DEVELOPMENT,
    "project": "proj-456",
    "created_by": "product_manager_1",
}


def _make_task(**overrides: object) -> Task:
    """Create a Task with sensible defaults, applying overrides."""
    kwargs = {**_TASK_KWARGS, **overrides}
    return Task(**kwargs)  # type: ignore[arg-type]


# ── AcceptanceCriterion ──────────────────────────────────────────


@pytest.mark.unit
class TestAcceptanceCriterion:
    def test_defaults(self) -> None:
        ac = AcceptanceCriterion(description="Tests pass")
        assert ac.description == "Tests pass"
        assert ac.met is False

    def test_met_flag(self) -> None:
        ac = AcceptanceCriterion(description="Tests pass", met=True)
        assert ac.met is True

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceCriterion(description="")

    def test_whitespace_description_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            AcceptanceCriterion(description="   ")

    def test_frozen(self) -> None:
        ac = AcceptanceCriterion(description="Tests pass")
        with pytest.raises(ValidationError):
            ac.met = True  # type: ignore[misc]

    def test_factory(self) -> None:
        from tests.unit.core.conftest import AcceptanceCriterionFactory

        ac = AcceptanceCriterionFactory.build()
        assert isinstance(ac, AcceptanceCriterion)
        assert len(ac.description) >= 1

    def test_json_roundtrip(self) -> None:
        ac = AcceptanceCriterion(description="Coverage >80%", met=True)
        json_str = ac.model_dump_json()
        restored = AcceptanceCriterion.model_validate_json(json_str)
        assert restored == ac


# ── Task: Construction & Defaults ────────────────────────────────


@pytest.mark.unit
class TestTaskConstruction:
    def test_minimal_valid_task(self) -> None:
        task = _make_task()
        assert task.id == "task-123"
        assert task.title == "Implement user authentication"
        assert task.type is TaskType.DEVELOPMENT
        assert task.status is TaskStatus.CREATED

    def test_all_fields_set(self) -> None:
        task = Task(
            id="task-999",
            title="Full task",
            description="A complete task",
            type=TaskType.RESEARCH,
            priority=Priority.CRITICAL,
            project="proj-1",
            created_by="pm-1",
            assigned_to="dev-1",
            reviewers=("reviewer-1", "reviewer-2"),
            dependencies=("task-100", "task-200"),
            artifacts_expected=(ExpectedArtifact(type=ArtifactType.CODE, path="src/"),),
            acceptance_criteria=(AcceptanceCriterion(description="Tests pass"),),
            estimated_complexity=Complexity.EPIC,
            budget_limit=50.0,
            deadline="2026-12-31",
            status=TaskStatus.ASSIGNED,
        )
        assert task.priority is Priority.CRITICAL
        assert task.assigned_to == "dev-1"
        assert len(task.reviewers) == 2
        assert len(task.dependencies) == 2
        assert len(task.artifacts_expected) == 1
        assert len(task.acceptance_criteria) == 1
        assert task.estimated_complexity is Complexity.EPIC
        assert task.budget_limit == 50.0
        assert task.deadline == "2026-12-31"
        assert task.status is TaskStatus.ASSIGNED

    def test_default_values(self) -> None:
        task = _make_task()
        assert task.priority is Priority.MEDIUM
        assert task.assigned_to is None
        assert task.reviewers == ()
        assert task.dependencies == ()
        assert task.artifacts_expected == ()
        assert task.acceptance_criteria == ()
        assert task.estimated_complexity is Complexity.MEDIUM
        assert task.budget_limit == 0.0
        assert task.deadline is None
        assert task.status is TaskStatus.CREATED


# ── Task: String Validation ──────────────────────────────────────


@pytest.mark.unit
class TestTaskStringValidation:
    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_task(id="")

    def test_whitespace_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(id="   ")

    def test_whitespace_title_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(title="   ")

    def test_whitespace_description_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(description="   ")

    def test_whitespace_project_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(project="   ")

    def test_whitespace_created_by_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(created_by="   ")

    def test_whitespace_assigned_to_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(
                assigned_to="   ",
                status=TaskStatus.ASSIGNED,
            )

    def test_empty_deadline_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="deadline must not be whitespace-only"
        ):
            _make_task(deadline="")

    def test_whitespace_deadline_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="deadline must not be whitespace-only"
        ):
            _make_task(deadline="   ")

    def test_invalid_deadline_format_rejected(self) -> None:
        with pytest.raises(ValidationError, match="valid ISO 8601"):
            _make_task(deadline="not-a-date")

    def test_valid_iso_date_deadline_accepted(self) -> None:
        task = _make_task(deadline="2026-12-31")
        assert task.deadline == "2026-12-31"

    def test_valid_iso_datetime_deadline_accepted(self) -> None:
        task = _make_task(deadline="2026-12-31T23:59:59")
        assert task.deadline == "2026-12-31T23:59:59"

    def test_whitespace_reviewer_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_task(reviewers=("valid", "   "))

    def test_empty_dependency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            _make_task(dependencies=("task-1", ""))


# ── Task: Dependency Validation ──────────────────────────────────


@pytest.mark.unit
class TestTaskDependencies:
    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot depend on itself"):
            _make_task(dependencies=("task-123",))

    def test_duplicate_dependencies_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in dependencies"):
            _make_task(dependencies=("task-1", "task-2", "task-1"))

    def test_valid_dependencies(self) -> None:
        task = _make_task(dependencies=("task-100", "task-200", "task-300"))
        assert task.dependencies == ("task-100", "task-200", "task-300")


# ── Task: Reviewer Validation ────────────────────────────────────


@pytest.mark.unit
class TestTaskReviewers:
    def test_duplicate_reviewers_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in reviewers"):
            _make_task(reviewers=("agent-1", "agent-2", "agent-1"))

    def test_valid_reviewers(self) -> None:
        task = _make_task(reviewers=("agent-1", "agent-2"))
        assert task.reviewers == ("agent-1", "agent-2")


# ── Task: Assignment/Status Consistency ──────────────────────────


@pytest.mark.unit
class TestTaskAssignmentConsistency:
    def test_created_with_assigned_to_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="assigned_to must be None when status is 'created'",
        ):
            _make_task(assigned_to="agent-1", status=TaskStatus.CREATED)

    def test_assigned_without_assigned_to_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="assigned_to is required when status is 'assigned'",
        ):
            _make_task(status=TaskStatus.ASSIGNED)

    def test_in_progress_without_assigned_to_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="assigned_to is required when status is 'in_progress'",
        ):
            _make_task(status=TaskStatus.IN_PROGRESS)

    def test_in_review_without_assigned_to_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="assigned_to is required when status is 'in_review'",
        ):
            _make_task(status=TaskStatus.IN_REVIEW)

    def test_completed_without_assigned_to_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="assigned_to is required when status is 'completed'",
        ):
            _make_task(status=TaskStatus.COMPLETED)

    def test_blocked_without_assigned_to_allowed(self) -> None:
        task = _make_task(status=TaskStatus.BLOCKED)
        assert task.assigned_to is None
        assert task.status is TaskStatus.BLOCKED

    def test_blocked_with_assigned_to_allowed(self) -> None:
        task = _make_task(assigned_to="agent-1", status=TaskStatus.BLOCKED)
        assert task.assigned_to == "agent-1"

    def test_cancelled_without_assigned_to_allowed(self) -> None:
        task = _make_task(status=TaskStatus.CANCELLED)
        assert task.assigned_to is None
        assert task.status is TaskStatus.CANCELLED

    def test_cancelled_with_assigned_to_allowed(self) -> None:
        task = _make_task(assigned_to="agent-1", status=TaskStatus.CANCELLED)
        assert task.assigned_to == "agent-1"


# ── Task: Budget ─────────────────────────────────────────────────


@pytest.mark.unit
class TestTaskBudget:
    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_task(budget_limit=-1.0)

    def test_zero_budget_allowed(self) -> None:
        task = _make_task(budget_limit=0.0)
        assert task.budget_limit == 0.0

    def test_positive_budget(self) -> None:
        task = _make_task(budget_limit=99.99)
        assert task.budget_limit == 99.99


# ── Task: Immutability ───────────────────────────────────────────


@pytest.mark.unit
class TestTaskImmutability:
    def test_frozen(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError):
            task.title = "New title"  # type: ignore[misc]

    def test_frozen_status(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError):
            task.status = TaskStatus.ASSIGNED  # type: ignore[misc]


# ── Task: Factory ────────────────────────────────────────────────


@pytest.mark.unit
class TestTaskFactory:
    def test_factory(self) -> None:
        from tests.unit.core.conftest import TaskFactory

        task = TaskFactory.build()
        assert isinstance(task, Task)
        assert isinstance(task.type, TaskType)
        assert task.status is TaskStatus.CREATED
        assert task.assigned_to is None


# ── Task: Serialization ─────────────────────────────────────────


@pytest.mark.unit
class TestTaskSerialization:
    def test_json_roundtrip(self) -> None:
        task = Task(
            id="task-rt",
            title="Roundtrip test",
            description="Test JSON roundtrip",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-1",
            created_by="pm-1",
            assigned_to="dev-1",
            reviewers=("reviewer-1",),
            dependencies=("task-dep-1",),
            artifacts_expected=(ExpectedArtifact(type=ArtifactType.CODE, path="src/"),),
            acceptance_criteria=(AcceptanceCriterion(description="Tests pass"),),
            estimated_complexity=Complexity.COMPLEX,
            budget_limit=5.0,
            deadline="2026-06-30",
            status=TaskStatus.ASSIGNED,
        )
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.id == task.id
        assert restored.type is task.type
        assert restored.priority is task.priority
        assert restored.assigned_to == task.assigned_to
        assert restored.reviewers == task.reviewers
        assert restored.dependencies == task.dependencies
        assert restored.artifacts_expected == task.artifacts_expected
        assert restored.acceptance_criteria == task.acceptance_criteria
        assert restored.status is task.status

    def test_model_dump(self) -> None:
        task = _make_task()
        dumped = task.model_dump()
        assert dumped["id"] == "task-123"
        assert dumped["status"] == "created"
        assert dumped["type"] == "development"
        assert dumped["priority"] == "medium"


# ── Task: Fixture ────────────────────────────────────────────────


@pytest.mark.unit
class TestTaskFixtures:
    def test_sample_task_fixture(self, sample_task: Task) -> None:
        assert sample_task.id == "task-123"
        assert sample_task.status is TaskStatus.CREATED
        assert sample_task.assigned_to is None

    def test_sample_assigned_task_fixture(self, sample_assigned_task: Task) -> None:
        assert sample_assigned_task.status is TaskStatus.ASSIGNED
        assert sample_assigned_task.assigned_to == "sarah_chen"


# ── Task: with_transition ───────────────────────────────────────


@pytest.mark.unit
class TestTaskWithTransition:
    """Tests for Task.with_transition() state machine enforcement."""

    def test_valid_transition_created_to_assigned(self) -> None:
        """Allow valid transition from CREATED to ASSIGNED."""
        task = _make_task()
        new_task = task.with_transition(TaskStatus.ASSIGNED, assigned_to="agent-1")
        assert new_task.status is TaskStatus.ASSIGNED
        assert new_task.assigned_to == "agent-1"
        assert new_task.id == task.id

    def test_valid_transition_assigned_to_in_progress(self) -> None:
        """Allow valid transition from ASSIGNED to IN_PROGRESS."""
        task = _make_task(assigned_to="agent-1", status=TaskStatus.ASSIGNED)
        new_task = task.with_transition(TaskStatus.IN_PROGRESS)
        assert new_task.status is TaskStatus.IN_PROGRESS

    def test_invalid_transition_created_to_completed(self) -> None:
        """Reject invalid transition from CREATED to COMPLETED."""
        task = _make_task()
        with pytest.raises(ValueError, match="Invalid task status transition"):
            task.with_transition(TaskStatus.COMPLETED, assigned_to="agent-1")

    def test_invalid_transition_from_terminal(self) -> None:
        """Reject transition from terminal state COMPLETED."""
        task = _make_task(assigned_to="agent-1", status=TaskStatus.COMPLETED)
        with pytest.raises(ValueError, match="Invalid task status transition"):
            task.with_transition(TaskStatus.ASSIGNED)

    def test_status_override_rejected(self) -> None:
        """Reject explicit status override in overrides."""
        task = _make_task()
        with pytest.raises(ValueError, match="status override is not allowed"):
            task.with_transition(
                TaskStatus.ASSIGNED,
                status=TaskStatus.COMPLETED,
                assigned_to="agent-1",
            )

    def test_validators_enforced_on_transition(self) -> None:
        """Ensure validators run on the new instance (assigned_to required)."""
        task = _make_task()
        with pytest.raises(
            (ValueError, ValidationError),
            match="assigned_to is required",
        ):
            task.with_transition(TaskStatus.ASSIGNED)

    def test_original_unchanged(self) -> None:
        """Ensure the original task is not modified (immutability)."""
        task = _make_task()
        new_task = task.with_transition(TaskStatus.ASSIGNED, assigned_to="agent-1")
        assert task.status is TaskStatus.CREATED
        assert task.assigned_to is None
        assert new_task is not task


# ── Logging tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestTaskLogging:
    def test_status_changed_event_on_transition(self) -> None:
        task = _make_task()
        with structlog.testing.capture_logs() as cap:
            task.with_transition(TaskStatus.ASSIGNED, assigned_to="agent-1")
        events = [e for e in cap if e.get("event") == TASK_STATUS_CHANGED]
        assert len(events) == 1
        assert events[0]["task_id"] == "task-123"
        assert events[0]["from_status"] == "created"
        assert events[0]["to_status"] == "assigned"
