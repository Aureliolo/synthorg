"""Promotion configuration models.

Defines ``PromotionConfig`` and sub-configs for controlling
promotion/demotion behavior.
"""

import copy
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import SeniorityLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001


class PromotionCriteriaConfig(BaseModel):
    """Configuration for promotion criteria evaluation.

    Attributes:
        min_criteria_met: Minimum number of criteria that must be met.
        required_criteria: Criteria names that must always be met.
    """

    model_config = ConfigDict(frozen=True)

    min_criteria_met: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Minimum number of criteria that must be met (max 3)",
    )
    required_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Criteria names that must always be met",
    )


class PromotionApprovalConfig(BaseModel):
    """Configuration for promotion approval decisions.

    Attributes:
        human_approval_from_level: Seniority level from which human
            approval is required for promotion.
        auto_demote_cost_saving: Auto-apply cost-saving demotions.
        human_demote_authority: Require human approval for
            authority-reducing demotions.
    """

    model_config = ConfigDict(frozen=True)

    human_approval_from_level: SeniorityLevel = Field(
        default=SeniorityLevel.SENIOR,
        description="Level from which human approval is required",
    )
    auto_demote_cost_saving: bool = Field(
        default=True,
        description="Auto-apply cost-saving demotions",
    )
    human_demote_authority: bool = Field(
        default=True,
        description="Human approval for authority-reducing demotions",
    )


class ModelMappingConfig(BaseModel):
    """Configuration for model mapping on seniority changes.

    Attributes:
        model_follows_seniority: Whether model changes with seniority.
        seniority_model_map: Explicit level-to-model overrides.
    """

    model_config = ConfigDict(frozen=True)

    model_follows_seniority: bool = Field(
        default=True,
        description="Whether model follows seniority level",
    )
    seniority_model_map: Any = Field(
        default_factory=dict,
        description="Explicit seniority level to model ID overrides "
        "(wrapped as MappingProxyType after validation)",
    )

    @model_validator(mode="after")
    def _validate_model_map_keys(self) -> Self:
        """Validate keys and wrap in MappingProxyType for immutability."""
        raw_map = self.seniority_model_map
        if isinstance(raw_map, MappingProxyType):
            raw_map = dict(raw_map)
        valid_values = {level.value for level in SeniorityLevel}
        for key in raw_map:
            if key not in valid_values:
                msg = f"Unknown seniority level in model map: {key!r}"
                raise ValueError(msg)
        # Wrap in MappingProxyType for read-only enforcement
        object.__setattr__(
            self,
            "seniority_model_map",
            MappingProxyType(copy.deepcopy(raw_map)),
        )
        return self


class PromotionConfig(BaseModel):
    """Top-level promotion/demotion configuration.

    Attributes:
        enabled: Whether the promotion subsystem is enabled.
        cooldown_hours: Hours between consecutive promotions/demotions.
        criteria: Promotion criteria configuration.
        approval: Promotion approval configuration.
        model_mapping: Model mapping configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    enabled: bool = Field(
        default=True,
        description="Whether the promotion subsystem is enabled",
    )
    cooldown_hours: int = Field(
        default=24,
        ge=0,
        description="Hours between consecutive promotions/demotions",
    )
    criteria: PromotionCriteriaConfig = Field(
        default_factory=PromotionCriteriaConfig,
        description="Promotion criteria configuration",
    )
    approval: PromotionApprovalConfig = Field(
        default_factory=PromotionApprovalConfig,
        description="Promotion approval configuration",
    )
    model_mapping: ModelMappingConfig = Field(
        default_factory=ModelMappingConfig,
        description="Model mapping configuration",
    )
