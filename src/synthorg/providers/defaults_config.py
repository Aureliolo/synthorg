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
        fallback_max_output_tokens: Floor for ``max_output_tokens`` when a
            driver cannot discover a per-model cap from its metadata
            source (e.g. LiteLLM has no data for the model, which is the
            case for every model behind an OpenAI-compatible endpoint).
            The effective cap is the larger of this and a value derived from
            the model's own ``max_context``, so lowering it cannot starve a
            large-context model. The model's ``max_context`` then caps the
            result, and that cap is the one thing this value cannot lift: on
            a model whose whole context is smaller than this, the effective
            cap lands BELOW the configured figure.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    fallback_max_output_tokens: int = Field(
        default=4096,
        gt=0,
        le=32_768,
        description=(
            "Floor for max output tokens when a model's metadata source "
            "exposes neither max_output_tokens nor max_tokens; the "
            "effective cap is derived from the model's context window."
        ),
    )
