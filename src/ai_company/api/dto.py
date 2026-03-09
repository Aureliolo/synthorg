"""Request/response DTOs and envelope models.

Response envelopes wrap all API responses in a consistent structure.
Request DTOs define write-operation payloads (separate from domain
models because they omit server-generated fields).
"""

from pydantic import BaseModel, ConfigDict, Field

from ai_company.core.enums import (
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from ai_company.core.types import NotBlankStr  # noqa: TC001

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200


# ── Response envelopes ──────────────────────────────────────────


class ApiResponse[T](BaseModel):
    """Standard API response envelope.

    Attributes:
        success: Whether the request succeeded.
        data: Response payload (``None`` on error).
        error: Error message (``None`` on success).
    """

    model_config = ConfigDict(frozen=True)

    success: bool = True
    data: T | None = None
    error: str | None = None


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
        success: Whether the request succeeded.
        data: Page of items.
        error: Error message (``None`` on success).
        pagination: Pagination metadata.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = True
    data: tuple[T, ...] = ()
    error: str | None = None
    pagination: PaginationMeta


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

    title: NotBlankStr
    description: NotBlankStr
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
    """

    model_config = ConfigDict(frozen=True)

    title: NotBlankStr | None = None
    description: NotBlankStr | None = None
    priority: Priority | None = None
    assigned_to: NotBlankStr | None = None
    budget_limit: float | None = Field(default=None, ge=0.0)


class TransitionTaskRequest(BaseModel):
    """Payload for a task status transition.

    Attributes:
        target_status: The desired target status.
        assigned_to: Optional assignee override for the transition.
    """

    model_config = ConfigDict(frozen=True)

    target_status: TaskStatus
    assigned_to: NotBlankStr | None = None
