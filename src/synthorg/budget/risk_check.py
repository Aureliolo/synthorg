"""Risk budget check result model."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskCheckResult(BaseModel):
    """Result of a risk budget pre-flight check.

    Attributes:
        allowed: Whether the action is within risk budget.
        risk_units: Estimated risk units for the action.
        reason: Human-readable explanation when denied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    allowed: bool = True
    risk_units: float = Field(default=0.0, ge=0.0)
    reason: str = ""

    @model_validator(mode="after")
    def _validate_reason_on_denial(self) -> Self:
        """Ensure denied results include a reason."""
        if not self.allowed and not self.reason.strip():
            msg = "reason must be non-empty when allowed is False"
            raise ValueError(msg)
        return self
