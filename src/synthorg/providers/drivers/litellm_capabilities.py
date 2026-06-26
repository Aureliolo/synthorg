"""Capability resolution for the LiteLLM driver.

Builds :class:`ModelCapabilities` from a resolved model config. Ollama is
sourced exclusively from our own ``/api/show`` discovery (never LiteLLM's
static model DB, which has no entry for locally-pulled models); other
providers use LiteLLM's static model info with the persisted metadata as
the fallback.
"""

from synthorg.config.provider_schema import ProviderModelConfig
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
    fallback_max_output_tokens: int,
) -> ModelCapabilities:
    """Construct ``ModelCapabilities`` from a resolved model config.

    Shared between single ``_do_get_model_capabilities`` and the batched
    ``batch_get_capabilities`` so both paths produce identical results.

    Args:
        model_config: The resolved provider model config.
        routing_key: LiteLLM routing key (``litellm_provider`` or the
            provider name); ``"ollama"`` bypasses the static DB.
        provider_name: Owning provider name for the capability record.
        fallback_max_output_tokens: Default output cap when the metadata
            omits one.

    Returns:
        A ``ModelCapabilities`` built from the config + discovery metadata.
    """
    litellm_model = f"{routing_key}/{model_config.id}"
    # Ollama is served exclusively by our own ``/api/show`` discovery, never
    # LiteLLM's static model DB: that DB has no entry for locally-pulled
    # models and would overwrite real probe capabilities (tools / vision /
    # reasoning / embedding) with all-False guesses.
    info = {} if routing_key == "ollama" else get_litellm_model_info(litellm_model)
    # An empty info dict (ollama, offline, or unknown model) would rebuild
    # all-False capability flags and discard the persisted metadata; fall back
    # to the config-layer record so capabilities survive.
    metadata = (
        extract_model_metadata(
            info,
            litellm_provider=routing_key,
            model_id=model_config.id,
            parser=get_family_parser(),
        )
        if info
        else model_config.metadata
    )

    max_output = metadata.max_output_tokens or fallback_max_output_tokens
    streaming_raw = info.get("supports_native_streaming")
    supports_streaming = True if streaming_raw is None else bool(streaming_raw)
    supports_tools = metadata.supports_tools
    # Detect embedding models, preferring the ollama ``/api/show`` probe
    # (authoritative for local models), then LiteLLM's ``mode`` (known cloud
    # models), then an id-substring last resort.
    supports_embeddings = (
        metadata.supports_embeddings
        or str(info.get("mode") or "") == "embedding"
        or "embed" in model_config.id.lower()
    )

    return ModelCapabilities(
        model_id=model_config.id,
        provider=provider_name,
        max_context_tokens=model_config.max_context,
        max_output_tokens=min(max_output, model_config.max_context),
        supports_tools=supports_tools,
        supports_vision=metadata.supports_vision,
        supports_streaming=supports_streaming,
        supports_streaming_tool_calls=supports_tools and supports_streaming,
        supports_system_messages=bool(info.get("supports_system_messages", True)),
        supports_embeddings=supports_embeddings,
        supports_reasoning=metadata.supports_reasoning,
        cost_per_1k_input=model_config.cost_per_1k_input,
        cost_per_1k_output=model_config.cost_per_1k_output,
    )
