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
        supports_tools: Model supports function/tool calling.
        supports_vision: Model accepts image inputs.
        supports_reasoning: Model exposes extended reasoning.
        max_output_tokens: Maximum output tokens, when known.
        family: Parsed model family (e.g. ``"claude-sonnet"``).
        generation: Parsed generation/recency as a sortable number
            (e.g. ``4.5`` for a ``4-5`` version), higher is newer.
        release_date: Parsed release date, when derivable from the id.
        metadata_source: Provenance of this metadata record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    supports_tools: bool = Field(default=False)
    supports_vision: bool = Field(default=False)
    supports_reasoning: bool = Field(default=False)
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Maximum output tokens, when known",
    )
    family: NotBlankStr | None = Field(
        default=None,
        description="Parsed model family (e.g. 'claude-sonnet')",
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
