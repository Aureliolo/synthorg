"""Token savings metrics for progressive tool disclosure.

Tracks per-session token usage across L1/L2/L3 tiers to measure
the context cost reduction vs full upfront loading.

Wire-up to ``EfficiencyRatios.tool_disclosure_token_savings`` is
pending.
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ToolDisclosureMetrics(BaseModel):
    """Per-session token savings from progressive disclosure.

    Attributes:
        l1_tokens_injected: Tokens used for L1 metadata in the
            system prompt.
        l2_tokens_loaded: Tokens used for on-demand L2 bodies.
        l3_tokens_fetched: Tokens used for explicit L3 resources.
        estimated_eager_tokens: What full upfront loading would
            cost in tokens.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    l1_tokens_injected: int = Field(
        default=0,
        ge=0,
        description="Tokens for L1 metadata",
    )
    l2_tokens_loaded: int = Field(
        default=0,
        ge=0,
        description="Tokens for loaded L2 bodies",
    )
    l3_tokens_fetched: int = Field(
        default=0,
        ge=0,
        description="Tokens for fetched L3 resources",
    )
    estimated_eager_tokens: int = Field(
        default=0,
        ge=0,
        description="Full upfront load token cost",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Tokens saved vs eager loading",
    )
    @property
    def token_savings(self) -> int:
        """Tokens saved compared to full upfront loading."""
        used = self.l1_tokens_injected + self.l2_tokens_loaded + self.l3_tokens_fetched
        return max(0, self.estimated_eager_tokens - used)
