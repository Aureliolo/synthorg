"""Last-resort provider model defaults.

Narrow frozen Pydantic config class carrying values that drivers
consult only when no per-model metadata is available (e.g. LiteLLM's
model-info registry returns an empty dict).  Lives on the provider
config tree so a deployment running long-context models can raise the
fallback without editing source.
"""

from pydantic import BaseModel, ConfigDict, Field


class ProviderModelDefaults(BaseModel):
    """Provider-wide defaults applied when model metadata is absent.

    Attributes:
        fallback_max_output_tokens: Default ``max_output_tokens`` used
            when a driver cannot discover a per-model cap from its
            metadata source (e.g. LiteLLM has no data for the model).
            Capped against the model's own ``max_context`` by the
            driver so this default never lifts a hard model limit.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    fallback_max_output_tokens: int = Field(
        default=4096,
        gt=0,
        le=32_768,
        description=(
            "Fallback max output tokens when a model's metadata source "
            "exposes neither max_output_tokens nor max_tokens."
        ),
    )
