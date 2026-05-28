"""Request/response DTOs for the training API endpoints."""

from typing import Annotated

from annotated_types import Gt
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import ContentType, TrainingPlanStatus

PositiveInt = Annotated[int, Gt(0)]


class CreateTrainingPlanRequest(BaseModel):
    """Request body for creating a training plan."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    override_sources: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Explicit source agent IDs",
    )
    content_types: tuple[ContentType, ...] | None = Field(
        default=None,
        description="Enable specific content types",
    )
    custom_caps: dict[ContentType, PositiveInt] | None = Field(
        default=None,
        description="Per-content-type cap overrides (positive integers)",
    )
    skip_training: bool = Field(
        default=False,
        description="Skip training entirely",
    )
    require_review: bool = Field(
        default=True,
        description="Require human review",
    )


class UpdateTrainingOverridesRequest(BaseModel):
    """Request body for updating training plan overrides."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    override_sources: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Updated source agent IDs",
    )
    custom_caps: dict[ContentType, PositiveInt] | None = Field(
        default=None,
        description="Updated per-content-type caps (positive integers)",
    )
    content_types: tuple[ContentType, ...] | None = Field(
        default=None,
        description="Updated enabled content types",
    )
    skip_training: bool | None = Field(
        default=None,
        description="Updated skip_training flag",
    )


class TrainingPlanResponse(BaseModel):
    """Response body for a training plan.

    Attributes:
        id: Plan identifier and idempotency key.
        new_agent_id: Agent being trained.
        new_agent_role: Role of the new hire.
        source_selector_type: Configured source selector strategy.
        enabled_content_types: Extractors enabled for the plan.
        curation_strategy_type: Configured curation strategy.
        volume_caps: Per-content-type hard limits (serialized).
        override_sources: Explicit source agent IDs.
        skip_training: Whether the plan is an explicit skip.
        require_review: Whether the review gate is enabled.
        status: Current plan lifecycle state.
        created_at: When the plan was created.
        executed_at: When the plan finished executing, if any.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    new_agent_id: NotBlankStr
    new_agent_role: NotBlankStr
    source_selector_type: NotBlankStr
    enabled_content_types: tuple[NotBlankStr, ...]
    curation_strategy_type: NotBlankStr
    volume_caps: tuple[tuple[NotBlankStr, int], ...]
    override_sources: tuple[NotBlankStr, ...]
    skip_training: bool
    require_review: bool
    status: TrainingPlanStatus
    created_at: AwareDatetime
    executed_at: AwareDatetime | None = None


class TrainingResultResponse(BaseModel):
    """Response body for a training result.

    Attributes:
        id: Unique result identifier.
        plan_id: Links to the executed training plan.
        new_agent_id: Agent that was trained.
        source_agents_used: Senior agents used as sources.
        items_extracted: Per-content-type extracted counts (serialized).
        items_after_curation: Per-content-type post-curation counts.
        items_after_guards: Per-content-type post-guard counts.
        items_stored: Per-content-type stored counts.
        approval_item_id: First approval id when review is pending.
        review_pending: ``True`` when the review gate blocked seeding.
        errors: Rejection reasons and store failure messages.
        started_at: Pipeline start timestamp.
        completed_at: Pipeline completion timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    plan_id: NotBlankStr
    new_agent_id: NotBlankStr
    source_agents_used: tuple[NotBlankStr, ...]
    items_extracted: tuple[tuple[NotBlankStr, int], ...]
    items_after_curation: tuple[tuple[NotBlankStr, int], ...]
    items_after_guards: tuple[tuple[NotBlankStr, int], ...]
    items_stored: tuple[tuple[NotBlankStr, int], ...]
    approval_item_id: NotBlankStr | None = None
    pending_approvals: tuple[tuple[NotBlankStr, NotBlankStr, int], ...] = ()
    review_pending: bool = False
    errors: tuple[str, ...] = ()
    started_at: AwareDatetime
    completed_at: AwareDatetime
