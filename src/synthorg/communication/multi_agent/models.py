"""What one agent returned from a single invocation."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class AgentResponse(BaseModel):
    """Result of a single agent invocation.

    Attributes:
        agent_id: Identifier of the agent that responded.
        content: Text content of the response.
        input_tokens: Tokens consumed by the prompt.
        output_tokens: Tokens generated in the response.
        cost: Estimated cost of the invocation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent that responded")
    content: str = Field(description="Response content")
    input_tokens: int = Field(
        default=0,
        ge=0,
        description="Prompt tokens consumed",
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="Response tokens generated",
    )
    cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated invocation cost",
    )
