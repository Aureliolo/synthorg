"""Task domain model and acceptance criteria."""

import copy
from collections import Counter
from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.artifact import ExpectedArtifact
from synthorg.core.enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    Stakes,
    TaskSource,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.task_transitions import validate_transition
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.task import TASK_STATUS_CHANGED
from synthorg.ontology.decorator import ontology_entity

logger = get_logger(__name__)


class AcceptanceCriterion(BaseModel):
    """A single acceptance criterion for a task.

    Attributes:
        description: The criterion text.
        met: Whether this criterion has been satisfied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    description: NotBlankStr = Field(
        description="Criterion text",
    )
    met: bool = Field(
        default=False,
        description="Whether this criterion has been satisfied",
    )


@ontology_entity
class Task(BaseModel):
    """A unit of work within the company.

    Represents a task from creation through completion, with full
    lifecycle tracking, dependency modeling, and acceptance criteria.
    Field schema matches the Engine design page.

    Attributes:
        id: Unique task identifier (e.g. ``"task-123"``).
        title: Short task title.
        description: Detailed task description.
        type: Classification of the task's work type.
        priority: Task urgency and importance level.
        project: Project ID this task belongs to.
        created_by: Agent name of the task creator.
        assigned_to: Agent ID of the assignee (``None`` if unassigned).
        reviewers: Agent IDs of designated reviewers.
        dependencies: IDs of tasks this task depends on.
        artifacts_expected: Artifacts expected to be produced.
        acceptance_criteria: Structured acceptance criteria.
        estimated_complexity: Task complexity estimate.
        budget_limit: Maximum spend for this task in the configured currency.
        deadline: Optional deadline (ISO 8601 string or ``None``).
        max_retries: Max reassignment attempts after failure (default 1).
        status: Current lifecycle status.
        parent_task_id: Parent task ID when created via delegation
            (``None`` for root tasks).
        delegation_chain: Ordered agent names of delegators (root first).
        task_structure: Classification of how subtasks relate to each
            other (``None`` when not yet classified).
        coordination_topology: Coordination topology for multi-agent
            execution (defaults to ``AUTO``).
        middleware_override: Per-task middleware chain override
            (``None`` uses company default chain).
        metadata: Arbitrary key-value metadata for pipeline tracking,
            labels, and operator-defined context.  Deep-copied at
            construction to prevent external mutation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Unique task identifier")
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
        description="Agent name of the task creator",
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
    stakes: Stakes = Field(
        default=Stakes.NORMAL,
        description=(
            "How consequential this task is, driving stakes-aware model"
            " routing (cheap model for low stakes, strong model plus"
            " red-team for high/critical stakes)"
        ),
    )
    budget_limit: float = Field(
        default=0.0,
        ge=0.0,
        description="Maximum spend for this task in the configured currency",
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
        description="Ordered agent names of delegators (root first)",
    )
    task_structure: TaskStructure | None = Field(
        default=None,
        description="Classification of subtask relationships (None = not classified)",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Coordination topology for multi-agent execution",
    )
    source: TaskSource | None = Field(
        default=None,
        description="Origin of this task (internal, client, or simulation)",
    )
    middleware_override: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description=("Per-task middleware chain override (None = use company default)"),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata for pipeline tracking and labels",
    )
    hard_ceiling: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Per-run hard real-money ceiling in the configured currency."
            " When the in-loop BudgetChecker observes accumulated_cost >="
            " hard_ceiling it raises RunHardCeilingExceededError and the"
            " engine parks the context via ApprovalGate so the operator"
            " can raise the ceiling and resume. None falls back to the"
            " global budget.run_hard_ceiling setting."
        ),
    )
    forecast_id: UUID | None = Field(
        default=None,
        description=(
            "Identifier of the pre-flight cost Forecast row this task"
            " was dispatched against. The work-entry adapter sets this"
            " after the operator approves the forecast; the engine"
            " plumbs it onto the parked-context payload when a ceiling"
            " halt occurs so the resume UI can show the original"
            " estimate alongside the accumulated cost."
        ),
    )

    @model_validator(mode="after")
    def _deepcopy_metadata(self) -> Self:
        """Defensive copy so callers cannot mutate the frozen model.

        Returns:
            The instance with ``metadata`` deep-copied so a caller's
            original dict cannot mutate the frozen model.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self

    @model_validator(mode="after")
    def _validate_deadline_format(self) -> Self:
        """Validate deadline format if present.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``deadline`` is whitespace-only or not a valid
                ISO 8601 string.
        """
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
        """Validate self-dependency and uniqueness.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the task depends on itself, or ``dependencies``
                or ``reviewers`` contain duplicate entries.
        """
        if str(self.id) in self.dependencies:
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
        """Validate delegation-related field constraints.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the task is its own parent, ``delegation_chain``
                has duplicates, or ``assigned_to`` also appears in the
                delegation chain.
        """
        if self.parent_task_id is not None and self.parent_task_id == str(self.id):
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
        ``COMPLETED``, ``AUTH_REQUIRED``) require ``assigned_to`` to be set.
        ``BLOCKED``, ``FAILED``, ``CANCELLED``, and ``REJECTED`` may or may
        not have an assignee.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``CREATED`` carries an assignee, or a status
                that requires an assignee has ``assigned_to=None``.
        """
        requires_assignee = {
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
            TaskStatus.COMPLETED,
            TaskStatus.AUTH_REQUIRED,
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

        Calls :func:`~synthorg.core.task_transitions.validate_transition`
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
