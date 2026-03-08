"""Task domain model and acceptance criteria."""

from collections import Counter
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.core.artifact import ExpectedArtifact  # noqa: TC001
from ai_company.core.enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from ai_company.core.task_transitions import validate_transition
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.task import TASK_STATUS_CHANGED

logger = get_logger(__name__)


class AcceptanceCriterion(BaseModel):
    """A single acceptance criterion for a task.

    Attributes:
        description: The criterion text.
        met: Whether this criterion has been satisfied.
    """

    model_config = ConfigDict(frozen=True)

    description: NotBlankStr = Field(
        description="Criterion text",
    )
    met: bool = Field(
        default=False,
        description="Whether this criterion has been satisfied",
    )


class Task(BaseModel):
    """A unit of work within the company.

    Represents a task from creation through completion, with full
    lifecycle tracking, dependency modeling, and acceptance criteria.
    Field schema matches DESIGN_SPEC Section 6.2.

    Attributes:
        id: Unique task identifier (e.g. ``"task-123"``).
        title: Short task title.
        description: Detailed task description.
        type: Classification of the task's work type.
        priority: Task urgency and importance level.
        project: Project ID this task belongs to.
        created_by: Agent ID of the task creator.
        assigned_to: Agent ID of the assignee (``None`` if unassigned).
        reviewers: Agent IDs of designated reviewers.
        dependencies: IDs of tasks this task depends on.
        artifacts_expected: Artifacts expected to be produced.
        acceptance_criteria: Structured acceptance criteria.
        estimated_complexity: Task complexity estimate.
        budget_limit: Maximum USD spend for this task.
        deadline: Optional deadline (ISO 8601 string or ``None``).
        max_retries: Max reassignment attempts after failure (default 1).
        status: Current lifecycle status.
        parent_task_id: Parent task ID when created via delegation
            (``None`` for root tasks).
        delegation_chain: Ordered agent IDs of delegators (root first).
        task_structure: Classification of how subtasks relate to each
            other (``None`` when not yet classified).
        coordination_topology: Coordination topology for multi-agent
            execution (defaults to ``AUTO``).
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(description="Unique task identifier")
    title: NotBlankStr = Field(description="Short task title")
    description: NotBlankStr = Field(
        description="Detailed task description",
    )
    type: TaskType = Field(description="Task work type")
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Task priority",
    )
    project: NotBlankStr = Field(
        description="Project ID this task belongs to",
    )
    created_by: NotBlankStr = Field(
        description="Agent ID of the task creator",
    )
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Agent ID of the assignee",
    )
    reviewers: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent IDs of designated reviewers",
    )
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of tasks this task depends on",
    )
    artifacts_expected: tuple[ExpectedArtifact, ...] = Field(
        default=(),
        description="Artifacts expected to be produced",
    )
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(
        default=(),
        description="Structured acceptance criteria",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Task complexity estimate",
    )
    budget_limit: float = Field(
        default=0.0,
        ge=0.0,
        description="Maximum USD spend for this task",
    )
    deadline: str | None = Field(
        default=None,
        description="Optional deadline (ISO 8601 string)",
    )
    max_retries: int = Field(
        default=1,
        ge=0,
        description="Max reassignment attempts after failure",
    )
    status: TaskStatus = Field(
        default=TaskStatus.CREATED,
        description="Current lifecycle status",
    )
    parent_task_id: NotBlankStr | None = Field(
        default=None,
        description="Parent task ID when created via delegation",
    )
    delegation_chain: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Ordered agent IDs of delegators (root first)",
    )
    task_structure: TaskStructure | None = Field(
        default=None,
        description="Classification of subtask relationships (None = not classified)",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Coordination topology for multi-agent execution",
    )

    @model_validator(mode="after")
    def _validate_deadline_format(self) -> Self:
        """Validate deadline format if present."""
        if self.deadline is not None:
            if not self.deadline.strip():
                msg = "deadline must not be whitespace-only"
                raise ValueError(msg)
            try:
                datetime.fromisoformat(self.deadline)
            except ValueError as exc:
                msg = f"deadline must be a valid ISO 8601 string, got {self.deadline!r}"
                raise ValueError(msg) from exc
        return self

    @model_validator(mode="after")
    def _validate_collections(self) -> Self:
        """Validate self-dependency and uniqueness."""
        if self.id in self.dependencies:
            msg = f"Task {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        if len(self.dependencies) != len(set(self.dependencies)):
            dupes = sorted(d for d, c in Counter(self.dependencies).items() if c > 1)
            msg = f"Duplicate entries in dependencies: {dupes}"
            raise ValueError(msg)
        if len(self.reviewers) != len(set(self.reviewers)):
            dupes = sorted(r for r, c in Counter(self.reviewers).items() if c > 1)
            msg = f"Duplicate entries in reviewers: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_delegation_fields(self) -> Self:
        """Validate delegation-related field constraints."""
        if self.parent_task_id is not None and self.parent_task_id == self.id:
            msg = f"Task {self.id!r} cannot be its own parent"
            raise ValueError(msg)
        if len(self.delegation_chain) != len(set(self.delegation_chain)):
            dupes = sorted(
                a for a, c in Counter(self.delegation_chain).items() if c > 1
            )
            msg = f"Duplicate entries in delegation_chain: {dupes}"
            raise ValueError(msg)
        if self.assigned_to is not None and self.assigned_to in self.delegation_chain:
            msg = (
                f"assigned_to {self.assigned_to!r} must not appear in delegation_chain"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_assignment_consistency(self) -> Self:
        """Ensure assigned_to is consistent with status.

        ``CREATED`` status must have ``assigned_to=None``.  Statuses beyond
        ``CREATED`` (``ASSIGNED``, ``IN_PROGRESS``, ``IN_REVIEW``,
        ``COMPLETED``) require ``assigned_to`` to be set.  ``BLOCKED``,
        ``FAILED``, and ``CANCELLED`` may or may not have an assignee.
        """
        requires_assignee = {
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
            TaskStatus.COMPLETED,
        }
        if self.status is TaskStatus.CREATED and self.assigned_to is not None:
            msg = "assigned_to must be None when status is 'created'"
            raise ValueError(msg)
        if self.status in requires_assignee and self.assigned_to is None:
            msg = f"assigned_to is required when status is {self.status.value!r}"
            raise ValueError(msg)
        return self

    def with_transition(self, target: TaskStatus, **overrides: Any) -> Task:
        """Create a new Task with a validated status transition.

        Calls :func:`~ai_company.core.task_transitions.validate_transition`
        before producing the new instance, ensuring the state machine is
        enforced.  Uses ``model_validate`` so all validators run on the
        new instance.

        Args:
            target: The desired target status.
            **overrides: Additional field overrides for the new task.

        Returns:
            A new Task with the target status.

        Raises:
            ValueError: If the transition is not valid or overrides
                contain ``status``.
        """
        if "status" in overrides:
            msg = "status override is not allowed; pass transition target explicitly"
            raise ValueError(msg)
        validate_transition(self.status, target)
        payload = self.model_dump()
        payload.update(overrides)
        payload["status"] = target
        result = Task.model_validate(payload)
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=self.id,
            from_status=self.status.value,
            to_status=target.value,
        )
        return result
