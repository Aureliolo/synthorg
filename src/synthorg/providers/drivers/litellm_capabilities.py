"""Capability resolution for the LiteLLM driver.

Builds :class:`ModelCapabilities` from a resolved model config. Ollama is
sourced exclusively from our own ``/api/show`` discovery (never LiteLLM's
static model DB, which has no entry for locally-pulled models); other
providers use LiteLLM's static model info with the persisted metadata as
the fallback.
"""

from synthorg.config.provider_schema import (
    ModelCapabilityOverrides,
    ProviderModelConfig,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_model_info import (
    extract_model_metadata,
    get_litellm_model_info,
)
from synthorg.providers.family_parser import get_family_parser


def build_capabilities(
    model_config: ProviderModelConfig,
    *,
    routing_key: str,
    provider_name: str,
) -> ModelCapabilities:
    """Construct ``ModelCapabilities`` from a resolved model config.

    Shared between single ``_do_get_model_capabilities`` and the batched
    ``batch_get_capabilities`` so both paths produce identical results.

    Args:
        model_config: The resolved provider model config.
        routing_key: LiteLLM routing key (``litellm_provider`` or the
            provider name); ``"ollama"`` bypasses the static DB.
        provider_name: Owning provider name for the capability record.

    Returns:
        A ``ModelCapabilities`` built from the config + discovery metadata.
    """
    litellm_model = f"{routing_key}/{model_config.id}"
    # Ollama is served exclusively by our own ``/api/show`` discovery, never
    # LiteLLM's static model DB: that DB has no entry for locally-pulled
    # models and would overwrite real probe capabilities (tools / vision /
    # reasoning / embedding) with all-False guesses.
    info = {} if routing_key == "ollama" else get_litellm_model_info(litellm_model)
    # A missing or partial info dict (ollama, offline, unknown model, or a
    # card silent on some fields) falls back per field to the persisted
    # config-layer record, so a probe or operator capability survives an
    # incomplete litellm entry rather than being discarded wholesale.
    metadata = extract_model_metadata(
        info,
        litellm_provider=routing_key,
        model_id=model_config.id,
        parser=get_family_parser(),
        base=model_config.metadata,
    )

    streaming_raw = info.get("supports_native_streaming")
    supports_streaming = True if streaming_raw is None else bool(streaming_raw)
    # Detect embedding models, preferring the ollama ``/api/show`` probe
    # (authoritative for local models), then LiteLLM's ``mode`` (known cloud
    # models), then an id-substring last resort.
    supports_embeddings = (
        metadata.supports_embeddings
        or str(info.get("mode") or "") == "embedding"
        or "embed" in model_config.id.lower()
    )
    # Image-output models: prefer the persisted metadata flag, then LiteLLM's
    # ``mode`` (which stamps ``"image_generation"`` on hosted image models).
    supports_image_generation = (
        metadata.supports_image_generation
        or str(info.get("mode") or "") == "image_generation"
    )

    overrides = model_config.capability_overrides or ModelCapabilityOverrides()

    def _overridden(*, resolved: bool, override: bool | None) -> bool:
        """Prefer the operator's declared value over the resolved one.

        Returns:
            *override* when the operator set one, else *resolved* unchanged.
        """
        return resolved if override is None else override

    return ModelCapabilities(
        model_id=model_config.id,
        provider=provider_name,
        max_context_tokens=model_config.max_context,
        supports_tools=_overridden(
            resolved=metadata.supports_tools, override=overrides.supports_tools
        ),
        supports_vision=_overridden(
            resolved=metadata.supports_vision, override=overrides.supports_vision
        ),
        supports_streaming=_overridden(
            resolved=supports_streaming, override=overrides.supports_streaming
        ),
        supports_embeddings=_overridden(
            resolved=supports_embeddings, override=overrides.supports_embeddings
        ),
        supports_image_generation=_overridden(
            resolved=supports_image_generation,
            override=overrides.supports_image_generation,
        ),
        supports_reasoning=_overridden(
            resolved=metadata.supports_reasoning,
            override=overrides.supports_reasoning,
        ),
        supports_prompt_caching=_overridden(
            resolved=metadata.supports_prompt_caching,
            override=overrides.supports_prompt_caching,
        ),
    )
