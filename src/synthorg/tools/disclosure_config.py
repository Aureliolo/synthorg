"""Configuration for progressive tool disclosure.

Controls token budgets and auto-unload behavior for the L1/L2/L3
disclosure system.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolDisclosureConfig(BaseModel):
    """Configuration for progressive tool disclosure.

    Attributes:
        l1_token_budget: Advisory maximum tokens for L1 metadata
            across all tools in the system prompt.  Used for
            capacity planning; not enforced at runtime.
        l2_token_budget: Advisory maximum tokens for loaded L2
            bodies in the provider API tools parameter.  Used for
            capacity planning; not enforced at runtime.
        auto_unload_on_budget_pressure: When ``True``, the oldest
            loaded L2 body is unloaded when context fill exceeds
            ``unload_threshold_percent``.
        unload_threshold_percent: Context fill percentage above
            which auto-unload triggers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    l1_token_budget: int = Field(
        default=3000,
        ge=500,
        le=20000,
        description="Max tokens for L1 metadata",
    )
    l2_token_budget: int = Field(
        default=15000,
        ge=1000,
        le=100000,
        description="Max tokens for loaded L2 bodies",
    )
    auto_unload_on_budget_pressure: bool = Field(
        default=True,
        description="Auto-unload oldest L2 on budget pressure",
    )
    unload_threshold_percent: float = Field(
        default=80.0,
        ge=50.0,
        le=99.0,
        description="Context fill % triggering auto-unload",
    )

    @model_validator(mode="after")
    def _validate_budget_order(self) -> ToolDisclosureConfig:
        """Ensure L2 budget is at least as large as L1 budget."""
        if self.l2_token_budget < self.l1_token_budget:
            msg = (
                f"l2_token_budget ({self.l2_token_budget}) must be "
                f">= l1_token_budget ({self.l1_token_budget})"
            )
            raise ValueError(msg)
        return self
