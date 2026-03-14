"""Task engine request, response, and event models.

All mutation requests are frozen Pydantic models, discriminated by a
``mutation_type`` literal.  Each request carries a ``request_id`` and
``requested_by`` field for tracing and auditing.
"""

import copy
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr  # noqa: TC001

MutationType = Literal["create", "update", "transition", "delete", "cancel"]
"""Discriminator literal for all mutation request types."""

TaskErrorCode = Literal["not_found", "version_conflict", "validation", "internal"]
"""Machine-readable error classification for mutation results."""

_MAX_TITLE_LENGTH: Final[int] = 256
"""Maximum length for task titles (matches API-layer ``CreateTaskRequest``)."""

_MAX_DESCRIPTION_LENGTH: Final[int] = 4096
"""Maximum length for task descriptions (matches API-layer ``CreateTaskRequest``)."""

_VALID_TASK_FIELDS: frozenset[str] = frozenset(Task.model_fields)
"""Field names accepted by ``model_fields`` on :class:`Task`.

Used to reject unknown keys in :class:`UpdateTaskMutation` and
:class:`TransitionTaskMutation` validators.
"""

# ── Mutation data ─────────────────────────────────────────────


class CreateTaskData(BaseModel):
    """Data required to create a new task (server-generated fields excluded).

    Mirrors :class:`~synthorg.api.dto.CreateTaskRequest` but lives in
    the engine layer so it has no dependency on the API (field parity is
    maintained by convention, not enforced).

    Note: ``CreateTaskRequest`` applies additional length constraints
    (``max_length``) at the API boundary.  This model enforces the same
    limits for defense-in-depth so engine-layer callers also benefit.

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    title: NotBlankStr = Field(
        max_length=_MAX_TITLE_LENGTH,
        description="Short task title",
    )
    description: NotBlankStr = Field(
        max_length=_MAX_DESCRIPTION_LENGTH,
        description="Detailed task description",
    )
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


_ALWAYS_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "created_by"})
"""Core identity fields that can never be changed via any mutation.

``status`` is also immutable for updates (must use transitions) and
for transition overrides (set via ``target_status``).  See
:data:`_IMMUTABLE_TASK_FIELDS` and :data:`_IMMUTABLE_OVERRIDE_FIELDS`.
"""

_IMMUTABLE_TASK_FIELDS: frozenset[str] = _ALWAYS_IMMUTABLE_FIELDS | {"status"}
"""Fields that must not be modified via :class:`UpdateTaskMutation`.

``status`` must go through :class:`TransitionTaskMutation` (which
validates the state machine); ``id`` and ``created_by`` are identity
fields set at creation time.
"""


class UpdateTaskMutation(BaseModel):
    """Request to update task fields.

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_id: Target task identifier.
        updates: Field-value pairs to apply (immutable fields rejected).
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

    @model_validator(mode="after")
    def _reject_immutable_fields(self) -> Self:
        unknown = set(self.updates) - _VALID_TASK_FIELDS
        if unknown:
            msg = f"Unknown task fields: {sorted(unknown)}"
            raise ValueError(msg)
        forbidden = set(self.updates) & _IMMUTABLE_TASK_FIELDS
        if forbidden:
            msg = f"Cannot update immutable fields: {sorted(forbidden)}"
            raise ValueError(msg)
        return self

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        # Deep-copy and wrap in MappingProxyType for full immutability.
        object.__setattr__(
            self,
            "updates",
            MappingProxyType(copy.deepcopy(dict(self.updates))),
        )


_IMMUTABLE_OVERRIDE_FIELDS: frozenset[str] = _ALWAYS_IMMUTABLE_FIELDS | {"status"}
"""Fields that must not be overridden during a transition.

See :data:`_ALWAYS_IMMUTABLE_FIELDS` for the shared base.
"""


class TransitionTaskMutation(BaseModel):
    """Request to perform a task status transition.

    Attributes:
        mutation_type: Discriminator literal.
        request_id: Unique request identifier for tracing.
        requested_by: Identity of the requester.
        task_id: Target task identifier.
        target_status: Desired target status.
        reason: Reason for the transition.
        overrides: Additional field overrides (immutable fields rejected).
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

    @model_validator(mode="after")
    def _reject_immutable_overrides(self) -> Self:
        unknown = set(self.overrides) - _VALID_TASK_FIELDS
        if unknown:
            msg = f"Unknown task fields in overrides: {sorted(unknown)}"
            raise ValueError(msg)
        forbidden = set(self.overrides) & _IMMUTABLE_OVERRIDE_FIELDS
        if forbidden:
            msg = f"Cannot override immutable fields: {sorted(forbidden)}"
            raise ValueError(msg)
        return self

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        # Deep-copy and wrap in MappingProxyType for full immutability.
        object.__setattr__(
            self,
            "overrides",
            MappingProxyType(copy.deepcopy(dict(self.overrides))),
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
        previous_status: Status before the mutation (``None`` on create
            or failure).
        error: Error description (``None`` on success).
        error_code: Machine-readable error classification for reliable
            dispatch (``None`` on success).
    """

    model_config = ConfigDict(frozen=True)

    request_id: NotBlankStr = Field(description="Echoed request identifier")
    success: bool = Field(description="Whether the mutation succeeded")
    task: Task | None = Field(default=None, description="Task after mutation")
    version: int = Field(default=0, ge=0, description="Version counter")
    previous_status: TaskStatus | None = Field(
        default=None,
        description="Status before mutation",
    )
    error: str | None = Field(default=None, description="Error description")
    error_code: TaskErrorCode | None = Field(
        default=None,
        description="Machine-readable error classification",
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.success and self.error is not None:
            msg = "Successful result must not carry an error"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "Failed result must carry an error description"
            raise ValueError(msg)
        if self.success and self.error_code is not None:
            msg = "Successful result must not carry an error_code"
            raise ValueError(msg)
        if not self.success and self.error_code is None:
            msg = "Failed result must carry an error_code"
            raise ValueError(msg)
        return self


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
        reason: Reason for transition/cancel (``None`` for other mutations).
        timestamp: When the mutation was applied.
    """

    model_config = ConfigDict(frozen=True)

    mutation_type: MutationType = Field(
        description="Mutation type that triggered event",
    )
    request_id: NotBlankStr = Field(description="Originating request identifier")
    requested_by: NotBlankStr = Field(description="Identity of the requester")
    task_id: NotBlankStr = Field(description="Task identifier (always present)")
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
    reason: str | None = Field(
        default=None,
        description="Reason for transition/cancel",
    )
    timestamp: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the mutation was applied",
    )
