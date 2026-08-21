"""Capability resolution for the LiteLLM driver.

Builds :class:`ModelCapabilities` from a resolved model config. Ollama is
sourced exclusively from our own ``/api/show`` discovery (never LiteLLM's
static model DB, which has no entry for locally-pulled models); other
providers use LiteLLM's static model info with the persisted metadata as
the fallback.
"""

from typing import Final

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_model_info import (
    extract_model_metadata,
    get_litellm_model_info,
)
from synthorg.providers.family_parser import get_family_parser

#: Fraction of the context window a model may spend on ONE response when no
#: metadata source publishes a per-model output cap.
_FALLBACK_CONTEXT_DIVISOR: Final[int] = 8

#: Ceiling on the derived allowance, so a single runaway turn cannot consume a
#: whole session budget on a million-token model.
_DERIVED_OUTPUT_CEILING: Final[int] = 65_536


def _fallback_output_tokens(*, max_context: int, configured: int) -> int:
    """Choose a per-response cap for a model whose metadata publishes none.

    DERIVED from the model's own window rather than taken flat, because a flat
    value ignores the one fact that decides whether it is survivable. An
    OpenAI-compatible endpoint ships no LiteLLM metadata, so its models all
    took the flat default; at 4096 that is fatal for a reasoning model, which
    spends the per-response budget on hidden reasoning BEFORE it can emit
    content or a tool call. A measured run had seven of eight agent sessions
    burn exactly 4096 completion tokens, emit no tool call at all, and be read
    by the loop as finished work, because a turn with no tool call is how a
    session says it is done. A truncated response is spent and then discarded,
    so a cap set too low costs more than it saves.

    The operator's configured value is a FLOOR, never a ceiling: this only ever
    widens the allowance, so a deployment that tuned it down for a small model
    keeps what it chose.

    Args:
        max_context: The model's declared context window.
        configured: The operator's configured fallback.

    Returns:
        The per-response cap, at least *configured*.
    """
    derived = min(_DERIVED_OUTPUT_CEILING, max_context // _FALLBACK_CONTEXT_DIVISOR)
    return max(configured, derived)


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
        fallback_max_output_tokens: Floor for the output cap when the
            metadata omits one; the effective value is derived from the
            model's own context window and is never below this.

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

    max_output = metadata.max_output_tokens or _fallback_output_tokens(
        max_context=model_config.max_context,
        configured=fallback_max_output_tokens,
    )
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
    # Image-output models: prefer the persisted metadata flag, then LiteLLM's
    # ``mode`` (which stamps ``"image_generation"`` on hosted image models).
    supports_image_generation = (
        metadata.supports_image_generation
        or str(info.get("mode") or "") == "image_generation"
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
        supports_image_generation=supports_image_generation,
        supports_reasoning=metadata.supports_reasoning,
        supports_prompt_caching=metadata.supports_prompt_caching,
        cost_per_1k_input=model_config.cost_per_1k_input,
        cost_per_1k_output=model_config.cost_per_1k_output,
    )
