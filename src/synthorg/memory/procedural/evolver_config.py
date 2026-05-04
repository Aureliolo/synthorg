"""Configuration for the autonomous skill evolver.

Safety rails are structurally enforced: ``requires_human_approval``
is ``Literal[True]`` and cannot be set to ``False``.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvolverConfig(BaseModel):
    """Configuration for the autonomous skill evolver.

    The evolver proposes org-scope skills for human approval.
    ``requires_human_approval`` is structurally enforced as
    ``Literal[True]`` -- the evolver has no write access to
    org memory.

    Attributes:
        enabled: Whether the evolver is active (opt-in).
        min_confidence_for_org_promotion: Minimum confidence
            score for org-scope promotion.
        min_agents_seen_pattern: Minimum distinct agents that
            must exhibit a pattern before proposing.
        max_proposals_per_cycle: Rate limit on proposals per
            evolution cycle.
        max_org_entries: Ceiling on total org-scope entries.
        requires_human_approval: Structurally enforced as True.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether the evolver is active (opt-in)",
    )
    min_confidence_for_org_promotion: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for org promotion",
    )
    min_agents_seen_pattern: int = Field(
        default=3,
        ge=1,
        description="Minimum distinct agents for a pattern",
    )
    max_proposals_per_cycle: int = Field(
        default=10,
        ge=1,
        description="Max proposals per evolution cycle",
    )
    max_org_entries: int = Field(
        default=10000,
        ge=1,
        description=(
            "Ceiling on org-scope entries. Reserved for future "
            "org-memory pruning -- AutonomousSkillEvolver is "
            "proposal-only and does not enforce this limit"
        ),
    )
    requires_human_approval: Literal[True] = Field(
        default=True,
        description=("Structurally enforced: evolver has no org memory write access"),
    )
