"""Cost record model for per-API-call tracking.

Implements DESIGN_SPEC Section 10.2: every API call is tracked as an
immutable cost record (append-only pattern).
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


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
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(min_length=1, description="Agent identifier")
    task_id: str = Field(min_length=1, description="Task identifier")
    provider: str = Field(min_length=1, description="LLM provider name")
    model: str = Field(min_length=1, description="Model identifier")
    input_tokens: int = Field(ge=0, description="Input token count")
    output_tokens: int = Field(ge=0, description="Output token count")
    cost_usd: float = Field(ge=0.0, description="Cost in USD")
    timestamp: AwareDatetime = Field(description="Timestamp of the API call")

    @model_validator(mode="after")
    def _validate_no_blank_strings(self) -> Self:
        """Ensure string identifier fields are not whitespace-only."""
        for field_name in ("agent_id", "task_id", "provider", "model"):
            if not getattr(self, field_name).strip():
                msg = f"{field_name} must not be whitespace-only"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_token_consistency(self) -> Self:
        """Ensure positive cost implies at least one non-zero token count."""
        if self.cost_usd > 0 and self.input_tokens == 0 and self.output_tokens == 0:
            msg = "cost_usd is positive but both token counts are zero"
            raise ValueError(msg)
        return self
