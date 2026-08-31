# module-kind: adapter
"""Capability-gated request features for the LiteLLM driver.

Keeps the reasoning-effort drop and prompt-cache placement (both gated
on the target model's resolved capabilities) out of the driver body so
the driver stays within its size budget. Both helpers are pure over
their inputs: the driver owns the capability lookup and passes it in.
"""

from collections.abc import Callable
from typing import Final, Literal

import litellm

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_REASONING_EFFORT_DROPPED,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_cache import apply_cache_control
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs
from synthorg.providers.models import CompletionConfig

logger = get_logger(__name__)

#: The request parameter carrying reasoning depth, as LiteLLM names it in the
#: per-route parameter lists this module consults. Typed as the literal so it
#: stays a valid ``_AcompletionKwargs`` key at the pop sites below.
_REASONING_PARAM: Final[Literal["reasoning_effort"]] = "reasoning_effort"


def route_carries_reasoning_effort(model_id: str, routing_key: str) -> bool:
    """Whether the target route will accept ``reasoning_effort`` at all.

    Our own capability metadata describes the *model*; whether the parameter
    survives the request is a property of the *route*. An OpenAI-compatible
    endpoint validates parameters against LiteLLM's view of the model, and a
    model absent from that view rejects ``reasoning_effort`` with a
    non-retryable error that fails the task on its first turn. So the route is
    asked directly, and its answer overrides ours.

    Returns:
        ``True`` when the route publishes a parameter list containing
        ``reasoning_effort``, and when it publishes no list at all. An empty
        answer means "unknown", not "refused": a route served by our own
        discovery rather than LiteLLM's static database has nothing to
        publish, and withholding reasoning from every model behind it would
        trade one silent degradation for another.
    """
    try:
        supported = litellm.get_supported_openai_params(
            model=model_id, custom_llm_provider=routing_key
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- a lookup that cannot answer is not evidence
        # the route refuses the parameter, and this must never fail a call.
        reraise_critical(exc)
        logger.debug(
            PROVIDER_REASONING_EFFORT_DROPPED,
            model=model_id,
            routing_key=routing_key,
            reason="param_lookup_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return True
    if not supported:
        return True
    return _REASONING_PARAM in supported


def apply_capability_gated_features(
    kwargs: _AcompletionKwargs,
    model_config: ProviderModelConfig,
    config: CompletionConfig | None,
    *,
    capabilities_provider: Callable[[ProviderModelConfig], ModelCapabilities],
    provider_name: str,
    routing_key: str,
) -> _AcompletionKwargs:
    """Drop or apply request features per the target model's capabilities.

    ``reasoning_effort`` is dropped for a model without reasoning support and
    for a route that will not carry the parameter, and ``cache_control``
    breakpoints are placed only for a caching-capable model. Capabilities are
    resolved once, and only when a gated feature is actually requested, so the
    common path stays free of the model-info lookup.

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
        kwargs.pop(_REASONING_PARAM, None)
        logger.info(
            PROVIDER_REASONING_EFFORT_DROPPED,
            provider=provider_name,
            model=model_config.id,
            reason="model_lacks_reasoning_support",
        )
    elif wants_reasoning and not route_carries_reasoning_effort(
        model_config.id, routing_key
    ):
        kwargs.pop(_REASONING_PARAM, None)
        logger.info(
            PROVIDER_REASONING_EFFORT_DROPPED,
            provider=provider_name,
            model=model_config.id,
            routing_key=routing_key,
            reason="route_rejects_parameter",
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
