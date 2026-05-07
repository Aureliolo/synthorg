"""Request/response DTOs and envelope models.

Response envelopes wrap all API responses in a consistent structure.
Request DTOs define write-operation payloads (separate from domain
models because they omit server-generated fields).
"""

from datetime import datetime
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ArtifactType,
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.core.validation import is_valid_action_type

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200

_MAX_METADATA_KEYS: int = 20
_MAX_METADATA_STR_LEN: int = 256


# ── Structured error detail (RFC 9457) ─────────────────────────


def _check_retry_after(*, retryable: bool, retry_after: int | None) -> None:
    """Validate ``retry_after``/``retryable`` consistency.

    Shared by ``ErrorDetail`` and ``ProblemDetail``.
    """
    if not retryable and retry_after is not None:
        msg = "retry_after must be None when retryable is False"
        raise ValueError(msg)


class ErrorDetail(BaseModel):
    """Structured error metadata (RFC 9457).

    Self-contained so agents can parse it without referencing
    the parent envelope.

    Attributes:
        detail: Human-readable occurrence-specific explanation.
        error_code: Machine-readable error code (by convention, 4-digit
            category-grouped; see ``ErrorCode``).
        error_category: High-level error category.
        retryable: Whether the client should retry the request.
        retry_after: Seconds to wait before retrying (``None``
            when not applicable).
        instance: Request correlation ID for log tracing.
        title: Static per-category title (e.g. "Authentication Error").
        type: Documentation URI for the error category.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    detail: NotBlankStr
    error_code: ErrorCode
    error_category: ErrorCategory
    retryable: bool = False
    retry_after: int | None = Field(
        default=None,
        ge=0,
        description="Seconds to wait before retrying (null when not applicable).",
    )
    instance: NotBlankStr
    title: NotBlankStr
    type: NotBlankStr

    @model_validator(mode="after")
    def _validate_retry_after_consistency(self) -> Self:
        """``retry_after`` must be ``None`` when ``retryable`` is ``False``."""
        _check_retry_after(retryable=self.retryable, retry_after=self.retry_after)
        return self


class ProblemDetail(BaseModel):
    """Bare RFC 9457 ``application/problem+json`` response body.

    Returned when the client sends ``Accept: application/problem+json``.

    Attributes:
        type: Documentation URI for the error category.
        title: Static per-category title.
        status: HTTP status code.
        detail: Human-readable occurrence-specific explanation.
        instance: Request correlation ID for log tracing.
        error_code: Machine-readable 4-digit error code.
        error_category: High-level error category.
        retryable: Whether the client should retry the request.
        retry_after: Seconds to wait before retrying (``None``
            when not applicable).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: NotBlankStr
    title: NotBlankStr
    status: int = Field(ge=400, le=599)
    detail: NotBlankStr
    instance: NotBlankStr
    error_code: ErrorCode
    error_category: ErrorCategory
    retryable: bool = False
    retry_after: int | None = Field(
        default=None,
        ge=0,
        description="Seconds to wait before retrying (null when not applicable).",
    )

    @model_validator(mode="after")
    def _validate_retry_after_consistency(self) -> Self:
        """``retry_after`` must be ``None`` when ``retryable`` is ``False``."""
        _check_retry_after(retryable=self.retryable, retry_after=self.retry_after)
        return self


# ── Response envelopes ──────────────────────────────────────────


class ApiResponse[T](BaseModel):
    """Standard API response envelope.

    Attributes:
        data: Response payload (``None`` on error).
        error: Operator-facing error message (``None`` on success). Must be
            either a static, generic, non-secret-bearing string or the
            output of ``safe_error_description(exc)`` (`from
            synthorg.observability import safe_error_description`); never
            raw ``str(exc)`` because the value is serialised over HTTP and
            can leak credential material from
            ``httpx.HTTPStatusError`` / ``psycopg.Error`` / OAuth provider
            exception messages otherwise.
        error_detail: Structured error metadata (``None`` on success).
        success: Whether the request succeeded (computed from ``error``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data: T | None = None
    error: str | None = None
    error_detail: ErrorDetail | None = None

    @model_validator(mode="after")
    def _validate_error_detail_consistency(self) -> Self:
        """``error_detail`` must not appear on a successful response."""
        if self.error_detail is not None and self.error is None:
            msg = "error_detail requires error to be set"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the request succeeded (derived from ``error``)."""
        return self.error is None


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses.

    Cursor-based: clients receive an opaque ``next_cursor`` and walk
    forward until ``has_more`` is ``False``.

    Attributes:
        limit: Maximum items per page.
        next_cursor: Opaque cursor for the next page (``None`` on the
            final page).
        has_more: Whether more items follow the current page.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    limit: int = Field(ge=1, description="Maximum items per page")
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page (null on final page)",
    )
    has_more: bool = Field(
        default=False,
        description="Whether more items follow the current page",
    )

    @model_validator(mode="after")
    def _validate_cursor_consistency(self) -> Self:
        """``has_more`` and ``next_cursor`` must agree.

        A non-null cursor means more pages exist; a null cursor means
        the caller is on the final page. Letting the two disagree would
        silently strand clients -- the envelope would advertise "more
        data" with no way to fetch it, or vice versa.
        """
        if self.has_more and self.next_cursor is None:
            msg = "has_more=True requires a non-null next_cursor"
            raise ValueError(msg)
        if not self.has_more and self.next_cursor is not None:
            msg = "has_more=False requires next_cursor to be None"
            raise ValueError(msg)
        return self


class PaginatedResponse[T](BaseModel):
    """Paginated API response envelope.

    Attributes:
        data: Page of items.
        error: Error message (``None`` on success).
        error_detail: Structured error metadata (``None`` on success).
        pagination: Pagination metadata.
        degraded_sources: Data sources that failed gracefully, resulting
            in partial data.  Empty when all sources responded normally.
        success: Whether the request succeeded (computed from ``error``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data: tuple[T, ...] = ()
    error: str | None = None
    error_detail: ErrorDetail | None = None
    pagination: PaginationMeta
    degraded_sources: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Data sources that failed gracefully (partial data)",
    )

    @model_validator(mode="after")
    def _validate_error_detail_consistency(self) -> Self:
        """Ensure ``error`` and ``error_detail`` are set together."""
        if self.error_detail is not None and self.error is None:
            msg = "error_detail requires error to be set"
            raise ValueError(msg)
        if self.error is not None and self.error_detail is None:
            msg = "error must be accompanied by error_detail"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the request succeeded (derived from ``error``)."""
        return self.error is None


# ── Artifact request DTOs ──────────────────────────────────────


class CreateArtifactRequest(BaseModel):
    """Payload for creating a new artifact.

    Attributes:
        type: Artifact type (code, tests, documentation).
        path: Logical file/directory path of the artifact.
        task_id: ID of the originating task.
        created_by: Agent ID of the creator.
        description: Human-readable description.
        content_type: MIME content type (empty if no content stored).
        project_id: Optional project ID to link the artifact to.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: ArtifactType = Field(
        description="Artifact category (code, tests, documentation).",
    )
    path: NotBlankStr = Field(
        max_length=1024,
        description="File path or artifact identifier within the workspace.",
    )
    task_id: NotBlankStr = Field(
        description="Originating task identifier.",
    )
    created_by: NotBlankStr = Field(
        description="Agent identifier of the artifact creator.",
    )
    description: str = Field(
        default="",
        max_length=4096,
        description="Human-readable artifact description.",
    )
    content_type: str = Field(
        default="",
        max_length=256,
        description=(
            "MIME type of the artifact content (empty when no content is stored)."
        ),
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Optional project identifier to link the artifact to.",
    )


# ── Project request DTOs ──────────────────────────────────────


class CreateProjectRequest(BaseModel):
    """Payload for creating a new project.

    Attributes:
        name: Project display name.
        description: Detailed project description.
        team: Agent IDs assigned to the project.
        lead: Agent ID of the project lead.
        deadline: Optional deadline (ISO 8601 string).
        budget: Total budget in base currency.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(max_length=256)
    description: str = Field(default="", max_length=4096)
    team: tuple[NotBlankStr, ...] = Field(default=(), max_length=50)
    lead: NotBlankStr | None = None
    deadline: str | None = None
    budget: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        """Validate deadline format and team uniqueness."""
        if self.deadline is not None:
            if not self.deadline.strip():
                msg = "deadline must not be whitespace-only"
                raise ValueError(msg)
            try:
                datetime.fromisoformat(self.deadline)
            except ValueError as exc:
                msg = f"deadline must be a valid ISO 8601 string, got {self.deadline!r}"
                raise ValueError(msg) from exc
        if len(self.team) != len(set(self.team)):
            seen: dict[str, int] = {}
            for member in self.team:
                seen[member] = seen.get(member, 0) + 1
            dupes = [k for k, v in seen.items() if v > 1]
            msg = f"team contains duplicate members: {dupes}"
            raise ValueError(msg)
        return self


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
        budget_limit: Maximum spend in base currency.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    All fields are optional -- only provided fields are updated.

    Attributes:
        title: New title.
        description: New description.
        priority: New priority.
        assigned_to: New assignee.
        budget_limit: New budget limit.
        expected_version: Optimistic concurrency guard.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action_type: NotBlankStr = Field(
        max_length=128,
        description="Kind of action requiring approval in `category:action` format.",
    )
    title: NotBlankStr = Field(
        max_length=256,
        description="Short human-readable summary of the approval.",
    )
    description: NotBlankStr = Field(
        max_length=4096,
        description="Detailed explanation of the action and why it requires approval.",
    )
    risk_level: ApprovalRiskLevel = Field(
        description="Assessed risk level for the action.",
    )
    ttl_seconds: int | None = Field(
        default=None,
        ge=60,
        le=604800,
        description=(
            "Optional time-to-live in seconds before the approval auto-expires "
            "(minimum 60, maximum 604800 = 7 days)."
        ),
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        max_length=128,
        description="Optional associated task identifier.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        max_length=_MAX_METADATA_KEYS,
        description=(
            "Optional key-value metadata for the approval "
            f"(max {_MAX_METADATA_KEYS} keys, "
            f"{_MAX_METADATA_STR_LEN}-char keys and values)."
        ),
    )

    @field_validator("action_type")
    @classmethod
    def _validate_action_type_format(cls, v: str) -> str:
        if not is_valid_action_type(v):
            msg = "action_type must use 'category:action' format"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _validate_metadata_bounds(self) -> Self:
        """Enforce per-entry size limits; key-count via Field(max_length=...)."""
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    comment: NotBlankStr | None = Field(default=None, max_length=4096)


class RejectRequest(BaseModel):
    """Payload for rejecting an approval item.

    Attributes:
        reason: Mandatory reason for rejection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reason: NotBlankStr = Field(max_length=4096)


# ── Coordination request/response DTOs ────────────────────────


class CoordinateTaskRequest(BaseModel):
    """Payload for triggering multi-agent coordination on a task.

    Attributes:
        agent_names: Agent names to coordinate with (``None`` = all active).
            When provided, must be non-empty and unique.
        max_subtasks: Maximum subtasks for decomposition.
        max_concurrency_per_wave: Override for max concurrency per wave.
        fail_fast: Override for fail-fast behaviour (``None`` = use
            section config default).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_names: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="Agent names to coordinate with (None = all active)",
    )
    max_subtasks: int = Field(default=10, ge=1, le=50)
    max_concurrency_per_wave: int | None = Field(
        default=None,
        ge=1,
        le=50,
    )
    fail_fast: bool | None = None

    @model_validator(mode="after")
    def _validate_unique_agent_names(self) -> Self:
        """Reject duplicate agent names."""
        if self.agent_names is not None:
            seen: set[str] = set()
            for name in self.agent_names:
                lower = name.lower()
                if lower in seen:
                    msg = f"Duplicate agent name: {name!r}"
                    raise ValueError(msg)
                seen.add(lower)
        return self


class CoordinationPhaseResponse(BaseModel):
    """Response model for a single coordination phase.

    Attributes:
        phase: Phase name.
        success: Whether the phase completed successfully.
        duration_seconds: Wall-clock duration of the phase.
        error: Error description if the phase failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    phase: NotBlankStr
    success: bool
    duration_seconds: float = Field(ge=0.0)
    error: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_success_error_consistency(self) -> Self:
        """Ensure success and error fields are consistent."""
        if self.success and self.error is not None:
            msg = "successful phase must not have an error"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "failed phase must have an error description"
            raise ValueError(msg)
        return self


class CoordinationResultResponse(BaseModel):
    """Response model for a complete coordination run.

    Attributes:
        parent_task_id: ID of the parent task.
        topology: Resolved coordination topology.
        total_duration_seconds: Total wall-clock duration.
        total_cost: Total cost across all waves.
        phases: Phase results in execution order.
        wave_count: Number of execution waves.
        is_success: Whether all phases succeeded (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    parent_task_id: NotBlankStr = Field(max_length=128)
    topology: NotBlankStr
    total_duration_seconds: float = Field(ge=0.0)
    total_cost: float = Field(ge=0.0)
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )
    phases: tuple[CoordinationPhaseResponse, ...] = Field(min_length=1)
    wave_count: int = Field(ge=0)

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether all phases succeeded",
    )
    @property
    def is_success(self) -> bool:
        """True when every phase completed successfully."""
        return all(p.success for p in self.phases)


# ── Provider management DTOs (split to dto_providers.py) ────
# Re-exported for backwards compatibility.
from synthorg.api.dto_providers import (  # noqa: E402
    CreateFromPresetRequest,
    CreateProviderRequest,
    DiscoverModelsResponse,
    ProbeLocalResponse,
    ProbePresetResponse,
    ProviderResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    UpdateProviderRequest,
    to_provider_response,
)


class RollbackAgentIdentityRequest(BaseModel):
    """Request body for rolling back an agent identity to a previous version.

    Attributes:
        target_version: Snapshot version number to restore content from
            (monotonic counter in the agent_identity_versions table).
        reason: Optional human-readable justification recorded alongside
            the evolution event for audit purposes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    target_version: int = Field(
        ge=1,
        description="Snapshot version to rollback to",
    )
    reason: NotBlankStr | None = Field(
        default=None,
        description="Optional rollback justification for the audit trail",
    )


__all__ = [
    "ApiResponse",
    "ApproveRequest",
    "CancelTaskRequest",
    "CoordinateTaskRequest",
    "CoordinationPhaseResponse",
    "CoordinationResultResponse",
    "CreateApprovalRequest",
    "CreateArtifactRequest",
    "CreateFromPresetRequest",
    "CreateProjectRequest",
    "CreateProviderRequest",
    "CreateTaskRequest",
    "DiscoverModelsResponse",
    "ErrorDetail",
    "PaginatedResponse",
    "PaginationMeta",
    "ProbeLocalResponse",
    "ProbePresetResponse",
    "ProblemDetail",
    "ProviderResponse",
    "RejectRequest",
    "RollbackAgentIdentityRequest",
    "TestConnectionRequest",
    "TestConnectionResponse",
    "TransitionTaskRequest",
    "UpdateProviderRequest",
    "UpdateTaskRequest",
    "to_provider_response",
]
