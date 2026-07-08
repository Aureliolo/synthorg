"""Per-model capability and family metadata.

Separated from :mod:`synthorg.config.provider_schema` so the provider
schema module stays under its size budget.  ``ModelMetadata`` is the
*config-layer* record persisted alongside each model; it is distinct
from the routing-layer :class:`synthorg.providers.capabilities.ModelCapabilities`,
which is built transiently per request and carries routing-only invariants.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr

MetadataSource = Literal["litellm", "preset", "probe", "unknown"]
"""Where a model's metadata was sourced from.

``litellm`` (enriched from the litellm model database at discovery),
``preset`` (baked into a provider preset), ``probe`` (filled by a live
presence/refresh probe), ``unknown`` (legacy or unenriched).
"""


class ModelMetadata(BaseModel):
    """Capability and family/generation metadata for a single model.

    Enriched from litellm at discovery and persisted on
    :class:`~synthorg.config.provider_schema.ProviderModelConfig` so the
    matcher can select on real capability data offline.

    Attributes:
        supports_tools: Model supports function/tool calling. The
            discovery-time claim (litellm / preset / probe / unknown); the
            matcher reads it optimistically for ``unknown`` sources.
        tool_calls_verified: Runtime-observed tool-calling truth, layered on
            top of ``supports_tools``. ``None`` = never observed (matcher uses
            the optimistic ``supports_tools`` path), ``True`` = a real tool
            call has been seen at runtime, ``False`` = repeated runtime
            tool-call failures proved the model cannot call tools (the matcher
            treats this as an authoritative hard-fail for ``requires_tools``
            agents, overriding optimism). Set by the runtime feedback loop
            (``providers.tool_call_feedback``); cleared back to ``None`` by a
            manual operator re-enable.
        supports_vision: Model accepts image inputs.
        supports_reasoning: Model exposes extended reasoning.
        supports_embeddings: Model is an embedding model (vector output).
        supports_image_generation: Model generates images from text prompts
            (image output modality).
        max_output_tokens: Maximum output tokens, when known.
        family: Parsed model family (e.g. ``"example-large"``).
        generation: Parsed generation/recency as a sortable number
            (e.g. ``4.5`` for a ``4-5`` version), higher is newer.
        parameter_count: Total model parameters, when known. A coarse
            size/strength signal the matcher uses to rank quality.
        cost_tier: Resource/pricing tier 1-4 (light -> extra heavy). For
            ollama this is the real per-model usage level scraped from the
            web page (the API does not expose it); for other providers it is
            derived from cost/size. Drives cost-aware tiering in the matcher.
        release_date: Parsed release date, when derivable from the id.
        metadata_source: Provenance of this metadata record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    supports_tools: bool = Field(default=False)
    tool_calls_verified: bool | None = Field(
        default=None,
        description=(
            "Runtime tool-calling truth: None=unobserved, True=proven, "
            "False=runtime-proven-incapable (authoritative matcher hard-fail)"
        ),
    )
    supports_vision: bool = Field(default=False)
    supports_reasoning: bool = Field(default=False)
    supports_embeddings: bool = Field(default=False)
    supports_image_generation: bool = Field(default=False)
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Maximum output tokens, when known",
    )
    parameter_count: int | None = Field(
        default=None,
        gt=0,
        description="Total model parameters, when known (size/strength signal)",
    )
    cost_tier: int | None = Field(
        default=None,
        ge=1,
        le=4,
        description="Resource/pricing tier 1-4 (light -> extra heavy)",
    )
    family: NotBlankStr | None = Field(
        default=None,
        description="Parsed model family (e.g. 'example-large')",
    )
    generation: float | None = Field(
        default=None,
        ge=0.0,
        description="Sortable generation/recency (higher is newer)",
    )
    release_date: date | None = Field(
        default=None,
        description="Parsed release date, when derivable from the id",
    )
    metadata_source: MetadataSource = Field(
        default="unknown",
        description="Provenance of this metadata record",
    )
