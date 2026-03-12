"""Task engine request, response, and event models.

All mutation requests are frozen Pydantic models, discriminated by a
``mutation_type`` literal.  Each request carries a ``request_id`` and
``requested_by`` field for tracing and auditing.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_company.core.enums import Complexity, Priority, TaskStatus, TaskType
from ai_company.core.task import Task  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001

# ── Mutation data ─────────────────────────────────────────────


class CreateTaskData(BaseModel):
    """Data required to create a new task (server-generated fields excluded).

    Mirrors :class:`~ai_company.api.dto.CreateTaskRequest` but lives in
    the engine layer so it has no dependency on the API.

    Attributes:
        title: Short task title.
        description: Detailed task description.
        type: Task work type.
        priority: Task priority level.
        project: Project ID.
        created_by: Agent name of the creator.
        assigned_to: Optional assignee agent ID.
        estimated_complexity: Complexity estimate.
        budget_limit: Maximum USD spend.
    """

    model_config = ConfigDict(frozen=True)

    title: NotBlankStr = Field(description="Short task title")
    description: NotBlankStr = Field(description="Detailed task description")
    type: TaskType = Field(description="Task work type")
    priority: Priority = Field(default=Priority.MEDIUM, description="Task priority")
    project: NotBlankStr = Field(description="Project ID")
    created_by: NotBlankStr = Field(description="Agent name of the creator")
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Assignee agent ID",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate",
    )
    budget_limit: float = Field(
        default=0.0,
        ge=0.0,
        description="Maximum USD spend",
    )


# ── Mutation requests ─────────────────────────────────────────


class CreateTaskMutation(BaseModel):
    """Request to create a new task.

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_data: Task creation payload.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: Literal["create"] = "create"
    request_id: NotBlankStr = Field(description="Unique request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task_data: CreateTaskData = Field(description="Task creation payload")


class UpdateTaskMutation(BaseModel):
    """Request to update task fields.

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_id: Target task identifier.
        updates: Field-value pairs to apply.
        expected_version: Optional optimistic concurrency version.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: Literal["update"] = "update"
    request_id: NotBlankStr = Field(description="Unique request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task_id: NotBlankStr = Field(description="Target task identifier")
    updates: dict[str, object] = Field(description="Field-value pairs to apply")
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Optional optimistic concurrency version",
    )


class TransitionTaskMutation(BaseModel):
    """Request to perform a task status transition.

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_id: Target task identifier.
        target_status: Desired target status.
        reason: Reason for the transition.
        overrides: Additional field overrides for the transition.
        expected_version: Optional optimistic concurrency version.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: Literal["transition"] = "transition"
    request_id: NotBlankStr = Field(description="Unique request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task_id: NotBlankStr = Field(description="Target task identifier")
    target_status: TaskStatus = Field(description="Desired target status")
    reason: NotBlankStr = Field(description="Reason for the transition")
    overrides: dict[str, object] = Field(
        default_factory=dict,
        description="Additional field overrides",
    )
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Optional optimistic concurrency version",
    )


class DeleteTaskMutation(BaseModel):
    """Request to delete a task.

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_id: Target task identifier.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: Literal["delete"] = "delete"
    request_id: NotBlankStr = Field(description="Unique request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task_id: NotBlankStr = Field(description="Target task identifier")


class CancelTaskMutation(BaseModel):
    """Request to cancel a task (shortcut for transition to CANCELLED).

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_id: Target task identifier.
        reason: Reason for cancellation.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: Literal["cancel"] = "cancel"
    request_id: NotBlankStr = Field(description="Unique request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task_id: NotBlankStr = Field(description="Target task identifier")
    reason: NotBlankStr = Field(description="Reason for cancellation")


TaskMutation = (
    CreateTaskMutation
    | UpdateTaskMutation
    | TransitionTaskMutation
    | DeleteTaskMutation
    | CancelTaskMutation
)
"""Union of all task mutation request types."""


# ── Mutation result ───────────────────────────────────────────


class TaskMutationResult(BaseModel):
    """Result of a processed task mutation.

    Attributes:
        request_id: Echoed request identifier.
        success: Whether the mutation succeeded.
        task: The task after mutation (``None`` on delete or failure).
        version: Current version counter for the task.
        error: Error description (``None`` on success).
    """

    model_config = ConfigDict(frozen=True)

    request_id: NotBlankStr = Field(description="Echoed request identifier")
    success: bool = Field(description="Whether the mutation succeeded")
    task: Task | None = Field(default=None, description="Task after mutation")
    version: int = Field(default=0, ge=0, description="Version counter")
    error: str | None = Field(default=None, description="Error description")


# ── State-change event ────────────────────────────────────────


class TaskStateChanged(BaseModel):
    """Event published to the message bus after each successful mutation.

    Attributes:
        mutation_type: Type of mutation that triggered the event.
        request_id: Originating request identifier.
        requested_by: Identity of the requester.
        task: Task snapshot after mutation (``None`` on delete).
        previous_status: Status before the mutation (``None`` on create).
        new_status: Status after the mutation (``None`` on delete).
        version: Version counter after mutation.
        timestamp: When the mutation was applied.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: str = Field(description="Mutation type that triggered event")
    request_id: NotBlankStr = Field(description="Originating request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task: Task | None = Field(
        default=None,
        description="Task snapshot after mutation",
    )
    previous_status: TaskStatus | None = Field(
        default=None,
        description="Status before mutation",
    )
    new_status: TaskStatus | None = Field(
        default=None,
        description="Status after mutation",
    )
    version: int = Field(ge=0, description="Version counter after mutation")
    timestamp: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the mutation was applied",
    )
