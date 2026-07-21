# module-kind: adapter
"""Capability-gated request features for the LiteLLM driver.

Keeps the reasoning-effort drop and prompt-cache placement (both gated
on the target model's resolved capabilities) out of the driver body so
the driver stays within its size budget. Both helpers are pure over
their inputs: the driver owns the capability lookup and passes it in.
"""

from collections.abc import Callable

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_REASONING_EFFORT_DROPPED,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_cache import apply_cache_control
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs
from synthorg.providers.models import CompletionConfig

logger = get_logger(__name__)


def apply_capability_gated_features(
    kwargs: _AcompletionKwargs,
    model_config: ProviderModelConfig,
    config: CompletionConfig | None,
    *,
    capabilities_provider: Callable[[ProviderModelConfig], ModelCapabilities],
    provider_name: str,
) -> _AcompletionKwargs:
    """Drop or apply request features per the target model's capabilities.

    ``reasoning_effort`` is dropped for a model without reasoning support,
    and ``cache_control`` breakpoints are placed only for a caching-capable
    model. Capabilities are resolved once, and only when a gated feature is
    actually requested, so the common path stays free of the model-info
    lookup.

    Returns:
        The kwargs mapping with unsupported features removed and supported
        ones applied.
    """
    if config is None:
        return kwargs
    wants_reasoning = config.reasoning_effort is not None
    wants_caching = config.prompt_caching
    if not wants_reasoning and not wants_caching:
        return kwargs

    capabilities = capabilities_provider(model_config)

    if wants_reasoning and not capabilities.supports_reasoning:
        kwargs.pop("reasoning_effort", None)
        logger.debug(
            PROVIDER_REASONING_EFFORT_DROPPED,
            provider=provider_name,
            model=model_config.id,
            reason="model_lacks_reasoning_support",
        )

    if wants_caching:
        apply_cache_control(
            kwargs,
            capabilities=capabilities,
            provider_name=provider_name,
            model_id=model_config.id,
        )
    return kwargs


def extract_raw_finish_reason(chunk: object) -> str | None:
    """Read the raw finish-reason string from a LiteLLM stream chunk.

    Returns:
        The first choice's ``finish_reason`` string when present, else
        ``None`` (intermediate chunks carry none).
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    reason = getattr(choices[0], "finish_reason", None)
    return reason if isinstance(reason, str) else None
