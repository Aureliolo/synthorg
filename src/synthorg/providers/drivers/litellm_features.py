# module-kind: adapter
"""Capability-gated request features for the LiteLLM driver.

Keeps the reasoning-effort drop and prompt-cache placement (both gated
on the target model's resolved capabilities) out of the driver body so
the driver stays within its size budget. Both helpers are pure over
their inputs: the driver owns the capability lookup and passes it in.
"""

from collections.abc import Callable
from enum import StrEnum
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
from synthorg.providers.drivers.litellm_model_info import litellm_knows_model
from synthorg.providers.models import CompletionConfig

logger = get_logger(__name__)

#: The request parameter carrying reasoning depth, as LiteLLM names it in the
#: per-route parameter lists this module consults. Typed as the literal so it
#: stays a valid ``_AcompletionKwargs`` key at the pop sites below.
_REASONING_PARAM: Final[Literal["reasoning_effort"]] = "reasoning_effort"


class RouteReasoningSupport(StrEnum):
    """What LiteLLM's view of the route says about ``reasoning_effort``.

    Three states, not two, and collapsing the last two is what made a declared
    treatment vanish: LiteLLM answers an unknown model with the ROUTE's generic
    parameter list, so "the list omits it" and "there is no list about this
    model" look identical while meaning opposite things.
    """

    #: LiteLLM lists the parameter for this model. Send it as-is.
    PUBLISHED = "published"
    #: LiteLLM knows this model and its list omits the parameter. It refuses.
    ABSENT = "absent"
    #: LiteLLM cannot speak for this model. Only our own metadata can.
    UNKNOWN = "unknown"


def route_reasoning_support(model_id: str, routing_key: str) -> RouteReasoningSupport:
    """Ask LiteLLM what it knows about ``reasoning_effort`` for this model.

    Our own capability metadata describes the *model*; whether the parameter
    survives the request is also a property of the *route*, because LiteLLM
    validates parameters against its own view before anything is sent and
    refuses an unlisted one with a non-retryable error that fails the task on
    its first turn. So the route is asked, and its answer is reported at the
    resolution it actually has.

    Returns:
        Which of the three states LiteLLM's view is in.
    """
    if not litellm_knows_model(model_id):
        # A model LiteLLM has no entry for does not get a per-model answer
        # below: it gets the ROUTE's generic list, identical for every unknown
        # model behind that route and naming no reasoning parameter, because a
        # generic OpenAI-compatible endpoint has none. That is a fact about the
        # route, not about this model, and reading it as this model's refusal
        # removes the parameter from every model behind every custom endpoint.
        return RouteReasoningSupport.UNKNOWN
    try:
        supported = litellm.get_supported_openai_params(
            model=model_id, custom_llm_provider=routing_key
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- a lookup that cannot answer is not evidence
        # the route refuses the parameter, and this must never fail a call.
        reraise_critical(exc)
        # WARNING rather than DEBUG: the fallback is safe (UNKNOWN forwards
        # the parameter rather than dropping it), but it changes what goes on
        # the wire, and it does so on EVERY call for as long as the lookup
        # keeps failing. At debug level a misconfigured route would rewrite
        # every request of every model behind it with nobody able to see it at
        # the levels anyone runs.
        logger.warning(
            PROVIDER_REASONING_EFFORT_DROPPED,
            model=model_id,
            routing_key=routing_key,
            reason="param_lookup_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return RouteReasoningSupport.UNKNOWN
    if not supported:
        # A route served by our own discovery rather than LiteLLM's static
        # database has nothing to publish, and withholding reasoning from every
        # model behind it would trade one silent degradation for another.
        return RouteReasoningSupport.UNKNOWN
    if _REASONING_PARAM in supported:
        return RouteReasoningSupport.PUBLISHED
    return RouteReasoningSupport.ABSENT


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
    for a route whose published parameters omit it, and declared allowed for a
    model LiteLLM cannot speak for, which is the only way it reaches an endpoint
    LiteLLM has no entry for. ``cache_control`` breakpoints are placed only for
    a caching-capable model. Capabilities are resolved once, and only when a
    gated feature is actually requested, so the common path stays free of the
    model-info lookup.

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
    elif wants_reasoning:
        support = route_reasoning_support(model_config.id, routing_key)
        if support is RouteReasoningSupport.ABSENT:
            kwargs.pop(_REASONING_PARAM, None)
            logger.info(
                PROVIDER_REASONING_EFFORT_DROPPED,
                provider=provider_name,
                model=model_config.id,
                routing_key=routing_key,
                reason="route_rejects_parameter",
            )
        elif support is RouteReasoningSupport.UNKNOWN:
            # LiteLLM cannot speak for this model, and its default when it
            # cannot is to refuse the parameter client-side rather than let the
            # endpoint answer. Declaring the parameter allowed is how LiteLLM is
            # told to forward it instead; measured against an endpoint that then
            # returns a reasoning field, where sending it undeclared raises
            # UnsupportedParamsError and dropping it degrades in silence. Our
            # own `supports_reasoning`, already required above, is the whole
            # basis for the claim, which is why this branch is unreachable
            # without it.
            kwargs["allowed_openai_params"] = [_REASONING_PARAM]

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
