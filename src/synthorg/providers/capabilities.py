"""Model capability descriptors for provider routing decisions."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class ModelCapabilities(BaseModel):
    """Static capability and cost metadata for a single LLM model.

    Used by the routing layer to decide which model handles a request
    based on required features (tools, vision, streaming) and cost.

    Attributes:
        model_id: Provider model identifier (e.g. ``"example-large-001"``).
        provider: Provider name (e.g. ``"example-provider"``).
        max_context_tokens: Maximum context window size in tokens.
        max_output_tokens: Maximum output tokens per request.
        supports_tools: Whether the model supports tool/function calling.
        supports_vision: Whether the model accepts image inputs.
        supports_streaming: Whether the model supports streaming responses.
        supports_streaming_tool_calls: Whether tool calls can be streamed.
        supports_system_messages: Whether system messages are accepted.
        cost_per_1k_input: Cost per 1 000 input tokens in USD.
        cost_per_1k_output: Cost per 1 000 output tokens in USD.
    """

    model_config = ConfigDict(frozen=True)

    model_id: NotBlankStr = Field(description="Model identifier")
    provider: NotBlankStr = Field(description="Provider name")
    max_context_tokens: int = Field(gt=0, description="Max context window tokens")
    max_output_tokens: int = Field(gt=0, description="Max output tokens per request")
    supports_tools: bool = Field(default=False, description="Supports tool calling")
    supports_vision: bool = Field(default=False, description="Supports image inputs")
    supports_streaming: bool = Field(
        default=True,
        description="Supports streaming responses",
    )
    supports_streaming_tool_calls: bool = Field(
        default=False,
        description="Supports streaming tool calls",
    )
    supports_system_messages: bool = Field(
        default=True,
        description="Supports system messages",
    )
    cost_per_1k_input: float = Field(
        ge=0.0,
        description="Cost per 1k input tokens in USD",
    )
    cost_per_1k_output: float = Field(
        ge=0.0,
        description="Cost per 1k output tokens in USD",
    )

    @model_validator(mode="after")
    def _validate_cross_field_constraints(self) -> Self:
        """Enforce cross-field consistency.

        Rules:
            - max_output_tokens must not exceed max_context_tokens.
            - supports_streaming_tool_calls requires both supports_tools
              and supports_streaming.

        Raises:
            ValueError: If any cross-field constraint is violated.
        """
        if self.max_output_tokens > self.max_context_tokens:
            msg = (
                f"max_output_tokens ({self.max_output_tokens}) must not "
                f"exceed max_context_tokens ({self.max_context_tokens})"
            )
            raise ValueError(msg)
        if self.supports_streaming_tool_calls and not self.supports_tools:
            msg = "supports_streaming_tool_calls requires supports_tools to be True"
            raise ValueError(msg)
        if self.supports_streaming_tool_calls and not self.supports_streaming:
            msg = "supports_streaming_tool_calls requires supports_streaming to be True"
            raise ValueError(msg)
        return self
