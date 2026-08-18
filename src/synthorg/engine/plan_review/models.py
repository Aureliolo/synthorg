# module-kind: code
"""Configuration for the stakeholder plan-review panel."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.session_budget import SessionCeilings

_DEFAULT_CEILINGS: SessionCeilings = SessionCeilings(cost_ceiling=1.0, token_ceiling=0)


class PlanReviewPanelConfig(BaseModel):
    """Bounds for the plan-review panel and each panellist's session.

    Attributes:
        panel_size: Maximum number of reviewers seated on the panel (the
            coordination group bound; the whole company means the relevant
            leads, not everyone).
        max_turns: Hard turn cap for each panellist's review session.
        max_revision_rounds: How many times a reviewed plan may be sent back
            to be re-planned before it is parked for the operator regardless.
            Zero makes the panel advisory, which is what it was before
            anything read its findings. It rides here rather than beside the
            spine because every other bound on a review round already does,
            and this config is the thing the reconciler rebuilds when the
            operator moves one.
        temperature: Sampling temperature for the review turns.
        ceilings: Both spend bounds on a panellist's session. One field, not
            two, so a wiring path that resolves the money bound cannot leave
            the token bound at its default without saying so: money measures
            nothing against a provider that bills by flat subscription, where
            cost never rises and the panellist's only other bound is its turn
            cap.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    panel_size: int = Field(default=4, ge=1, le=8, description="Maximum panel size")
    max_turns: int = Field(default=6, ge=1, le=50, description="Review turn cap")
    max_revision_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Re-plan rounds a panel verdict may drive",
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    ceilings: SessionCeilings = Field(
        default=_DEFAULT_CEILINGS,
        description="Per-reviewer money + token bounds",
    )
