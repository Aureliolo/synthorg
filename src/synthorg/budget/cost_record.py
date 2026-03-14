"""Cost record model for per-API-call tracking.

Implements the Cost Tracking section of the Operations design page:
every API call is tracked as an immutable cost record
(append-only pattern).
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.call_category import LLMCallCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class CostRecord(BaseModel):
    """Immutable record of a single API call's cost.

    Once created, a ``CostRecord`` cannot be modified (frozen model).
    This enforces the append-only pattern: new records are created for
    each API call; existing records are never updated.

    Attributes:
        agent_id: Agent identifier (string reference).
        task_id: Task identifier (string reference).
        provider: LLM provider name.
        model: Model identifier.
        input_tokens: Input token count.
        output_tokens: Output token count.
        cost_usd: Cost in USD.
        timestamp: Timezone-aware timestamp of the API call.
        call_category: Optional LLM call category for coordination
            metrics (productive, coordination, system).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    provider: NotBlankStr = Field(description="LLM provider name")
    model: NotBlankStr = Field(description="Model identifier")
    input_tokens: int = Field(ge=0, description="Input token count")
    output_tokens: int = Field(ge=0, description="Output token count")
    cost_usd: float = Field(ge=0.0, description="Cost in USD")
    timestamp: AwareDatetime = Field(description="Timestamp of the API call")
    call_category: LLMCallCategory | None = Field(
        default=None,
        description="LLM call category (productive, coordination, system)",
    )

    @model_validator(mode="after")
    def _validate_token_consistency(self) -> Self:
        """Ensure positive cost implies at least one non-zero token count."""
        if self.cost_usd > 0 and self.input_tokens == 0 and self.output_tokens == 0:
            msg = "cost_usd is positive but both token counts are zero"
            raise ValueError(msg)
        return self
