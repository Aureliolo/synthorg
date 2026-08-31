"""Model capability descriptors for provider routing decisions."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class ModelCapabilities(BaseModel):
    """Runtime-discovered capability flags for a single LLM model.

    Used by the routing layer to decide which model handles a request
    based on required features (tools, vision, embeddings, ...), and by
    the engine's budget gauge to size a run's context-window display. Cost
    and the output-token cap are read from the persisted
    :class:`~synthorg.config.provider_schema.ProviderModelConfig`, never
    from this transient, per-request record.

    Attributes:
        model_id: Provider model identifier (e.g. ``"example-expert-001"``).
        provider: Provider name (e.g. ``"example-provider"``).
        max_context_tokens: Maximum context window size in tokens.
        supports_tools: Whether the model supports tool/function calling.
        supports_vision: Whether the model accepts image inputs.
        supports_streaming: Whether the model supports streaming responses.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_id: NotBlankStr = Field(description="Model identifier")
    provider: NotBlankStr = Field(description="Provider name")
    max_context_tokens: int = Field(gt=0, description="Max context window tokens")
    supports_tools: bool = Field(default=False, description="Supports tool calling")
    supports_vision: bool = Field(default=False, description="Supports image inputs")
    supports_streaming: bool = Field(
        default=True,
        description="Supports streaming responses",
    )
    supports_embeddings: bool = Field(
        default=False,
        description="Is an embedding model (vector output, not chat)",
    )
    supports_image_generation: bool = Field(
        default=False,
        description="Generates images from text prompts (image output modality)",
    )
    supports_reasoning: bool = Field(
        default=False,
        description="Exposes extended reasoning (thinking/o1-style models)",
    )
    supports_prompt_caching: bool = Field(
        default=False,
        description="Can reuse a cached prompt prefix via cache_control",
    )
