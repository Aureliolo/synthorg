"""Risk record model for per-action risk tracking.

Implements the Risk Budget section of the Operations design page:
every agent action is tracked as an immutable risk record
(append-only pattern, parallel to ``CostRecord``).
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.security.risk_scorer import RiskScore


class RiskRecord(BaseModel):
    """Immutable record of a single action's risk assessment.

    Once created, a ``RiskRecord`` cannot be modified (frozen model).
    This enforces the append-only pattern: new records are created for
    each action; existing records are never updated.

    Attributes:
        agent_id: Agent identifier.
        task_id: Task identifier.
        action_type: The ``category:action`` string.
        risk_score: Full multi-dimensional risk assessment.
        risk_units: Scalar risk value for budget tracking.
        timestamp: Timezone-aware timestamp of the action.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    action_type: NotBlankStr = Field(
        description="Action type (category:action)",
    )
    risk_score: RiskScore = Field(
        description="Multi-dimensional risk assessment",
    )
    risk_units: float = Field(
        ge=0.0,
        description=(
            "Scalar risk value for budget tracking. Should match "
            "risk_score.risk_units; enforced by BudgetEnforcer."
        ),
    )
    timestamp: AwareDatetime = Field(description="Timestamp of the action")

    @model_validator(mode="after")
    def _validate_action_type_format(self) -> Self:
        """Ensure action_type follows the category:action convention.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        parts = self.action_type.split(":", maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():  # noqa: PLR2004
            msg = (
                f"action_type must follow category:action format "
                f"with non-empty segments, got {self.action_type!r}"
            )
            raise ValueError(msg)
        return self
