"""Promotion domain models.

Frozen Pydantic models for promotion criteria results, evaluations,
approval decisions, records, and requests.
"""

from typing import Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.enums import ApprovalStatus, SeniorityLevel, compare_seniority
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection


class CriterionResult(BaseModel):
    """Result of a single promotion/demotion criterion evaluation.

    Attributes:
        name: Criterion name.
        met: Whether the criterion was met.
        current_value: Agent's current value for this criterion.
        threshold: Required threshold value.
        weight: Weight of this criterion (None if not weighted).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr = Field(description="Criterion name")
    met: bool = Field(description="Whether the criterion was met")
    current_value: float = Field(description="Agent's current value")
    threshold: float = Field(description="Required threshold value")
    weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Weight of this criterion",
    )


class PromotionEvaluation(BaseModel):
    """Result of evaluating an agent for promotion or demotion.

    Attributes:
        agent_id: Agent being evaluated.
        current_level: Current seniority level.
        target_level: Target seniority level.
        direction: Whether this is a promotion or demotion.
        criteria_results: Individual criterion results.
        required_criteria_met: Whether all required criteria are met.
        eligible: Whether the agent is eligible for the change.
        evaluated_at: When the evaluation was performed.
        strategy_name: Strategy that performed the evaluation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent being evaluated")
    current_level: SeniorityLevel = Field(description="Current seniority level")
    target_level: SeniorityLevel = Field(description="Target seniority level")
    direction: PromotionDirection = Field(
        description="Promotion or demotion",
    )
    criteria_results: tuple[CriterionResult, ...] = Field(
        default=(),
        description="Individual criterion results",
    )
    required_criteria_met: bool = Field(
        description="Whether all required criteria are met",
    )
    eligible: bool = Field(
        description="Whether the agent is eligible for the change",
    )
    evaluated_at: AwareDatetime = Field(
        description="When the evaluation was performed",
    )
    strategy_name: NotBlankStr = Field(
        description="Strategy that performed the evaluation",
    )

    @model_validator(mode="after")
    def _validate_direction_consistency(self) -> Self:
        """Validate direction matches level ordering."""
        cmp = compare_seniority(self.target_level, self.current_level)
        if self.direction == PromotionDirection.PROMOTION and cmp <= 0:
            msg = "direction=PROMOTION requires target_level > current_level"
            raise ValueError(msg)
        if self.direction == PromotionDirection.DEMOTION and cmp >= 0:
            msg = "direction=DEMOTION requires target_level < current_level"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def criteria_met_count(self) -> int:
        """Number of criteria that were met."""
        return sum(1 for c in self.criteria_results if c.met)


class PromotionApprovalDecision(BaseModel):
    """Decision on whether a promotion needs human approval.

    Attributes:
        auto_approve: Whether the promotion can be auto-approved.
        reason: Explanation for the decision.
    """

    model_config = ConfigDict(frozen=True)

    auto_approve: bool = Field(description="Whether auto-approved")
    reason: NotBlankStr = Field(description="Explanation for the decision")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def requires_human(self) -> bool:
        """Whether human approval is required (inverse of auto_approve)."""
        return not self.auto_approve


class PromotionRecord(BaseModel):
    """Record of a completed promotion or demotion.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent who was promoted/demoted.
        agent_name: Agent display name.
        old_level: Previous seniority level.
        new_level: New seniority level.
        direction: Whether this was a promotion or demotion.
        evaluation: The evaluation that led to this change.
        approved_by: Who approved the change ("auto" if auto-approved,
            "human" if human-approved via approval_id).
        approval_id: Approval item ID if human-approved.
        effective_at: When the change took effect.
        initiated_by: Who initiated the promotion process.
        model_changed: Whether the model was changed.
        old_model_id: Previous model ID (None if not changed).
        new_model_id: New model ID (None if not changed).
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent who was promoted/demoted")
    agent_name: NotBlankStr = Field(description="Agent display name")
    old_level: SeniorityLevel = Field(description="Previous seniority level")
    new_level: SeniorityLevel = Field(description="New seniority level")
    direction: PromotionDirection = Field(
        description="Promotion or demotion",
    )
    evaluation: PromotionEvaluation = Field(
        description="Evaluation that led to this change",
    )
    approved_by: NotBlankStr | None = Field(
        default=None,
        description="Who approved the change",
    )
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Approval item ID if human-approved",
    )
    effective_at: AwareDatetime = Field(
        description="When the change took effect",
    )
    initiated_by: NotBlankStr = Field(
        description="Who initiated the promotion process",
    )
    model_changed: bool = Field(
        default=False,
        description="Whether the model was changed",
    )
    old_model_id: NotBlankStr | None = Field(
        default=None,
        description="Previous model ID",
    )
    new_model_id: NotBlankStr | None = Field(
        default=None,
        description="New model ID",
    )

    @model_validator(mode="after")
    def _validate_model_fields(self) -> Self:
        """Validate model_changed consistency with model ID fields."""
        if self.model_changed and (
            self.old_model_id is None or self.new_model_id is None
        ):
            msg = "model_changed=True requires both old_model_id and new_model_id"
            raise ValueError(msg)
        if self.model_changed and self.old_model_id == self.new_model_id:
            msg = "model_changed=True requires old_model_id and new_model_id to differ"
            raise ValueError(msg)
        if not self.model_changed and (
            self.old_model_id is not None or self.new_model_id is not None
        ):
            msg = "model_changed=False requires both model IDs to be None"
            raise ValueError(msg)
        return self


class PromotionRequest(BaseModel):
    """A pending promotion or demotion request.

    Attributes:
        id: Unique request identifier.
        agent_id: Agent being promoted/demoted.
        agent_name: Agent display name.
        current_level: Current seniority level.
        target_level: Target seniority level.
        direction: Whether this is a promotion or demotion.
        evaluation: The evaluation supporting this request.
        status: Current approval status.
        created_at: When the request was created.
        approval_id: Linked approval item ID (for human approval).
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique request identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent being promoted/demoted")
    agent_name: NotBlankStr = Field(description="Agent display name")
    current_level: SeniorityLevel = Field(description="Current seniority level")
    target_level: SeniorityLevel = Field(description="Target seniority level")
    direction: PromotionDirection = Field(
        description="Promotion or demotion",
    )
    evaluation: PromotionEvaluation = Field(
        description="Evaluation supporting this request",
    )
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Current approval status",
    )
    created_at: AwareDatetime = Field(description="When the request was created")
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Linked approval item ID",
    )
