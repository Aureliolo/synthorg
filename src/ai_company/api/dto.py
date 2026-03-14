"""Request/response DTOs and envelope models.

Response envelopes wrap all API responses in a consistent structure.
Request DTOs define write-operation payloads (separate from domain
models because they omit server-generated fields).
"""

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from ai_company.core.enums import (
    ApprovalRiskLevel,
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.core.validation import is_valid_action_type

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200

_MAX_METADATA_KEYS: int = 20
_MAX_METADATA_STR_LEN: int = 256


# ── Response envelopes ──────────────────────────────────────────


class ApiResponse[T](BaseModel):
    """Standard API response envelope.

    Attributes:
        data: Response payload (``None`` on error).
        error: Error message (``None`` on success).
        success: Whether the request succeeded (computed from ``error``).
    """

    model_config = ConfigDict(frozen=True)

    data: T | None = None
    error: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the request succeeded (derived from ``error``)."""
        return self.error is None


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses.

    Attributes:
        total: Total number of items matching the query.
        offset: Starting offset of the returned page.
        limit: Maximum items per page.
    """

    model_config = ConfigDict(frozen=True)

    total: int = Field(ge=0, description="Total matching items")
    offset: int = Field(ge=0, description="Starting offset")
    limit: int = Field(ge=1, description="Maximum items per page")


class PaginatedResponse[T](BaseModel):
    """Paginated API response envelope.

    Attributes:
        data: Page of items.
        error: Error message (``None`` on success).
        pagination: Pagination metadata.
        success: Whether the request succeeded (computed from ``error``).
    """

    model_config = ConfigDict(frozen=True)

    data: tuple[T, ...] = ()
    error: str | None = None
    pagination: PaginationMeta

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the request succeeded (derived from ``error``)."""
        return self.error is None


# ── Task request DTOs ───────────────────────────────────────────


class CreateTaskRequest(BaseModel):
    """Payload for creating a new task.

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

    title: NotBlankStr = Field(max_length=256)
    description: NotBlankStr = Field(max_length=4096)
    type: TaskType
    priority: Priority = Priority.MEDIUM
    project: NotBlankStr
    created_by: NotBlankStr
    assigned_to: NotBlankStr | None = None
    estimated_complexity: Complexity = Complexity.MEDIUM
    budget_limit: float = Field(default=0.0, ge=0.0)


class UpdateTaskRequest(BaseModel):
    """Payload for updating task fields.

    All fields are optional — only provided fields are updated.

    Attributes:
        title: New title.
        description: New description.
        priority: New priority.
        assigned_to: New assignee.
        budget_limit: New budget limit.
        expected_version: Optimistic concurrency guard.
    """

    model_config = ConfigDict(frozen=True)

    title: NotBlankStr | None = Field(default=None, max_length=256)
    description: NotBlankStr | None = Field(default=None, max_length=4096)
    priority: Priority | None = None
    assigned_to: NotBlankStr | None = None
    budget_limit: float | None = Field(default=None, ge=0.0)
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Optimistic concurrency version guard",
    )


class TransitionTaskRequest(BaseModel):
    """Payload for a task status transition.

    Attributes:
        target_status: The desired target status.
        assigned_to: Optional assignee override for the transition.
        expected_version: Optimistic concurrency guard.
    """

    model_config = ConfigDict(frozen=True)

    target_status: TaskStatus = Field(description="Desired target status")
    assigned_to: NotBlankStr | None = None
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Optimistic concurrency version guard",
    )


class CancelTaskRequest(BaseModel):
    """Payload for cancelling a task.

    Attributes:
        reason: Reason for cancellation.
    """

    model_config = ConfigDict(frozen=True)

    reason: NotBlankStr = Field(
        max_length=4096,
        description="Reason for cancellation",
    )


# ── Approval request DTOs ──────────────────────────────────────


class CreateApprovalRequest(BaseModel):
    """Payload for creating a new approval item.

    Attributes:
        action_type: Kind of action requiring approval
            (``category:action`` format).
        title: Short summary.
        description: Detailed explanation.
        risk_level: Assessed risk level.
        ttl_seconds: Optional time-to-live in seconds
            (min 60, max 604 800 = 7 days).
        task_id: Optional associated task.
        metadata: Additional key-value pairs.
    """

    model_config = ConfigDict(frozen=True)

    action_type: NotBlankStr = Field(max_length=128)
    title: NotBlankStr = Field(max_length=256)
    description: NotBlankStr = Field(max_length=4096)
    risk_level: ApprovalRiskLevel
    ttl_seconds: int | None = Field(default=None, ge=60, le=604800)
    task_id: NotBlankStr | None = Field(default=None, max_length=128)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("action_type")
    @classmethod
    def _validate_action_type_format(cls, v: str) -> str:
        if not is_valid_action_type(v):
            msg = "action_type must use 'category:action' format"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _validate_metadata_bounds(self) -> Self:
        """Limit metadata size to prevent memory abuse."""
        if len(self.metadata) > _MAX_METADATA_KEYS:
            msg = f"metadata must have at most {_MAX_METADATA_KEYS} keys"
            raise ValueError(msg)
        for k, v in self.metadata.items():
            if len(k) > _MAX_METADATA_STR_LEN:
                msg = f"metadata key must be at most {_MAX_METADATA_STR_LEN} characters"
                raise ValueError(msg)
            if len(v) > _MAX_METADATA_STR_LEN:
                msg = (
                    f"metadata value must be at most {_MAX_METADATA_STR_LEN} characters"
                )
                raise ValueError(msg)
        return self


class ApproveRequest(BaseModel):
    """Payload for approving an approval item.

    Attributes:
        comment: Optional comment explaining the approval.
    """

    model_config = ConfigDict(frozen=True)

    comment: NotBlankStr | None = Field(default=None, max_length=4096)


class RejectRequest(BaseModel):
    """Payload for rejecting an approval item.

    Attributes:
        reason: Mandatory reason for rejection.
    """

    model_config = ConfigDict(frozen=True)

    reason: NotBlankStr = Field(max_length=4096)
