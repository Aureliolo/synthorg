# module-kind: code
"""Configuration for the stakeholder plan-review panel."""

from pydantic import BaseModel, ConfigDict, Field


class PlanReviewPanelConfig(BaseModel):
    """Bounds for the plan-review panel and each panellist's session.

    Attributes:
        panel_size: Maximum number of reviewers seated on the panel (the
            coordination group bound; the whole company means the relevant
            leads, not everyone).
        max_turns: Hard turn cap for each panellist's review session.
        temperature: Sampling temperature for the review turns.
        cost_ceiling: Per-reviewer spend ceiling (base currency); a review
            session halts once accumulated cost reaches it.
        token_ceiling: Per-reviewer token ceiling. The money ceiling measures
            nothing against a provider that bills by flat subscription, where
            cost never rises; tokens are counted on every provider. 0
            disables it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    panel_size: int = Field(default=4, ge=1, le=8, description="Maximum panel size")
    max_turns: int = Field(default=6, ge=1, le=50, description="Review turn cap")
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    cost_ceiling: float = Field(
        default=1.0,
        gt=0.0,
        description="Per-reviewer spend ceiling in the base currency",
    )
    token_ceiling: int = Field(
        default=0,
        ge=0,
        description="Per-reviewer token ceiling; 0 disables it",
    )
