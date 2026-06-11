"""Resolved-autonomy value object.

``EffectiveAutonomy`` is the expanded, resolved autonomy an agent runs under,
produced by :class:`~synthorg.security.autonomy.resolver.AutonomyResolver`. It is
shared vocabulary the engine, workers, tools, and meta layers annotate against, so
it lives in a dependency-free ``core`` leaf (it needs only the autonomy-level enum)
rather than the heavy ``security`` package, which any consumer would otherwise have
to drag in to reference the type at module level.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.autonomy_enums import AutonomyLevel


class EffectiveAutonomy(BaseModel):
    """Resolved, expanded autonomy for an agent's execution run.

    Produced by :class:`~synthorg.security.autonomy.resolver.AutonomyResolver`
    by resolving the three-level chain (agent, department, company) and
    expanding category shortcuts into concrete action types.

    Attributes:
        level: Resolved autonomy level.
        auto_approve_actions: Concrete action types that are auto-approved.
        human_approval_actions: Concrete action types requiring human approval.
        security_agent: Whether the security agent reviews escalations.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: AutonomyLevel = Field(description="Resolved autonomy level")
    auto_approve_actions: frozenset[str] = Field(
        description="Expanded auto-approve action types",
    )
    human_approval_actions: frozenset[str] = Field(
        description="Expanded human-approval action types",
    )
    security_agent: bool = Field(
        description="Whether security agent reviews escalations",
    )

    @model_validator(mode="after")
    def _validate_disjoint(self) -> Self:
        """Ensure expanded action sets are disjoint.

        Returns:
            The validated resolved autonomy.

        Raises:
            ValueError: If an action appears in both expanded sets.
        """
        overlap = self.auto_approve_actions & self.human_approval_actions
        if overlap:
            msg = (
                f"auto_approve_actions and human_approval_actions must be "
                f"disjoint, overlapping: {sorted(overlap)}"
            )
            raise ValueError(msg)
        return self
