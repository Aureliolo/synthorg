"""Promotion API response DTOs.

Wire-facing projections of the promotion domain models
(:mod:`synthorg.hr.promotion.models`). Seniority levels and the
promotion direction are flattened to their string values so the
generated TypeScript client sees plain enums rather than nested
objects.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.hr.promotion.models import (
    CriterionResult,
    PromotionEvaluation,
    PromotionRecord,
    PromotionRequest,
)


class CriterionResultDTO(BaseModel):
    """A single promotion/demotion criterion outcome."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Criterion name")
    met: bool = Field(description="Whether the criterion was met")
    current_value: float = Field(description="Agent's current value for the criterion")
    threshold: float = Field(description="Required threshold value")
    weight: float | None = Field(
        default=None,
        description="Weight of this criterion, when weighted",
    )

    @classmethod
    def from_domain(cls, result: CriterionResult) -> CriterionResultDTO:
        """Project a domain criterion result onto the DTO.

        Returns:
            The wire-facing criterion result.
        """
        return cls(
            name=result.name,
            met=result.met,
            current_value=result.current_value,
            threshold=result.threshold,
            weight=result.weight,
        )


class PromotionEvaluationDTO(BaseModel):
    """Outcome of evaluating an agent for promotion or demotion."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent that was evaluated")
    current_level: NotBlankStr = Field(description="Current seniority level")
    target_level: NotBlankStr = Field(description="Target seniority level")
    direction: NotBlankStr = Field(description="Either 'promotion' or 'demotion'")
    eligible: bool = Field(description="Whether the agent qualifies for the change")
    required_criteria_met: bool = Field(
        description="Whether all required criteria were met",
    )
    criteria_met_count: int = Field(
        ge=0,
        description="Number of criteria that were met",
    )
    criteria_results: tuple[CriterionResultDTO, ...] = Field(
        default=(),
        description="Individual criterion outcomes",
    )
    evaluated_at: AwareDatetime = Field(description="When the evaluation ran")
    strategy_name: NotBlankStr = Field(
        description="Strategy that produced the evaluation",
    )

    @classmethod
    def from_domain(cls, evaluation: PromotionEvaluation) -> PromotionEvaluationDTO:
        """Project a domain evaluation onto the DTO.

        Returns:
            The wire-facing evaluation.
        """
        return cls(
            agent_id=evaluation.agent_id,
            current_level=NotBlankStr(evaluation.current_level.value),
            target_level=NotBlankStr(evaluation.target_level.value),
            direction=NotBlankStr(evaluation.direction.value),
            eligible=evaluation.eligible,
            required_criteria_met=evaluation.required_criteria_met,
            criteria_met_count=evaluation.criteria_met_count,
            criteria_results=tuple(
                CriterionResultDTO.from_domain(c) for c in evaluation.criteria_results
            ),
            evaluated_at=evaluation.evaluated_at,
            strategy_name=evaluation.strategy_name,
        )


class PromotionRecordDTO(BaseModel):
    """A completed promotion or demotion."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique record identifier")
    agent_id: NotBlankStr = Field(description="Agent that was promoted or demoted")
    agent_name: NotBlankStr = Field(description="Agent display name")
    old_level: NotBlankStr = Field(description="Previous seniority level")
    new_level: NotBlankStr = Field(description="New seniority level")
    direction: NotBlankStr = Field(description="Either 'promotion' or 'demotion'")
    approved_by: NotBlankStr | None = Field(
        default=None,
        description="Who approved the change ('auto' or 'human')",
    )
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Approval item identifier when human-approved",
    )
    effective_at: AwareDatetime = Field(description="When the change took effect")
    initiated_by: NotBlankStr = Field(description="Who initiated the change")
    model_changed: bool = Field(description="Whether the agent's model changed")
    old_model_id: NotBlankStr | None = Field(
        default=None,
        description="Previous model identifier, when changed",
    )
    new_model_id: NotBlankStr | None = Field(
        default=None,
        description="New model identifier, when changed",
    )

    @classmethod
    def from_domain(cls, record: PromotionRecord) -> PromotionRecordDTO:
        """Project a domain record onto the DTO.

        Returns:
            The wire-facing promotion record.
        """
        return cls(
            id=NotBlankStr(str(record.id)),
            agent_id=record.agent_id,
            agent_name=record.agent_name,
            old_level=NotBlankStr(record.old_level.value),
            new_level=NotBlankStr(record.new_level.value),
            direction=NotBlankStr(record.direction.value),
            approved_by=record.approved_by,
            approval_id=record.approval_id,
            effective_at=record.effective_at,
            initiated_by=record.initiated_by,
            model_changed=record.model_changed,
            old_model_id=record.old_model_id,
            new_model_id=record.new_model_id,
        )


class PromotionRequestDTO(BaseModel):
    """A pending or decided promotion/demotion request."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique request identifier")
    agent_id: NotBlankStr = Field(description="Agent being promoted or demoted")
    agent_name: NotBlankStr = Field(description="Agent display name")
    current_level: NotBlankStr = Field(description="Current seniority level")
    target_level: NotBlankStr = Field(description="Target seniority level")
    direction: NotBlankStr = Field(description="Either 'promotion' or 'demotion'")
    status: NotBlankStr = Field(description="Current approval status")
    created_at: AwareDatetime = Field(description="When the request was created")
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Linked approval item identifier, when human-gated",
    )

    @classmethod
    def from_domain(cls, request: PromotionRequest) -> PromotionRequestDTO:
        """Project a domain request onto the DTO.

        Returns:
            The wire-facing promotion request.
        """
        return cls(
            id=NotBlankStr(str(request.id)),
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            current_level=NotBlankStr(request.current_level.value),
            target_level=NotBlankStr(request.target_level.value),
            direction=NotBlankStr(request.direction.value),
            status=NotBlankStr(request.status.value),
            created_at=request.created_at,
            approval_id=request.approval_id,
        )


class PromotionApplyResultDTO(BaseModel):
    """Outcome of applying a promotion/demotion to a single agent.

    ``applied`` carries the record when the change auto-approved and took
    effect; it is ``None`` when the request needs human approval (the
    ``request`` status is then ``pending``) or was rejected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    request: PromotionRequestDTO = Field(description="The promotion request")
    applied: PromotionRecordDTO | None = Field(
        default=None,
        description="The applied record when auto-approved, else null",
    )
