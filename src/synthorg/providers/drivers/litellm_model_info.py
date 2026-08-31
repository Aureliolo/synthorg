# module-kind: code
"""LiteLLM static model-metadata lookup and field coercion.

Isolates the ``litellm.get_model_info`` query and the defensive
coercion of its loosely-typed fields so ``litellm_driver`` stays
focused on completion dispatch and response mapping.
"""

import math
from collections.abc import Mapping
from typing import Final

import litellm as _litellm

from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_INFO_UNAVAILABLE,
    PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
    PROVIDER_MODEL_PRICING_CONFLICT,
    PROVIDER_MODEL_PRICING_REGISTERED,
)
from synthorg.providers.family_parser import FamilyParser

logger = get_logger(__name__)

_LITELLM_DRIVER = "litellm"


def get_litellm_model_info(litellm_model: str) -> dict[str, object]:
    """Query LiteLLM for static model metadata.

    Returns empty dict when the model is unknown or the lookup raises;
    callers fall back to config defaults.

    Returns:
        A dict of LiteLLM model metadata fields, or an empty dict when
        the model is unknown or the lookup raises.
    """
    try:
        raw = _litellm.get_model_info(model=litellm_model)
        info: dict[str, object] = dict(raw) if raw else {}
    except KeyError, ValueError:
        logger.info(PROVIDER_MODEL_INFO_UNAVAILABLE, model=litellm_model)
        return {}
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        if _is_unmapped_model(exc):
            logger.info(PROVIDER_MODEL_INFO_UNAVAILABLE, model=litellm_model)
            return {}
        logger.warning(
            PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
            model=litellm_model,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}
    return info if isinstance(info, dict) else {}


#: How LiteLLM words the miss it raises for a model absent from its pricing
#: table. Matched on the message because it raises a bare ``Exception`` for
#: it, which is indistinguishable by type from a genuine fault.
_UNMAPPED_MODEL_MARKER: Final[str] = "isn't mapped yet"


def _is_unmapped_model(exc: Exception) -> bool:
    """Whether *exc* is LiteLLM saying it has no pricing for the model.

    That is an ordinary condition for a self-hosted or newly-released model
    and the caller already handles it by falling back to config defaults, so
    it belongs at INFO alongside the other "no metadata" answers. Logged as
    an unexpected error it fires on every single completion, burying the
    warnings that do mean something under ones that do not.

    Returns:
        Whether the exception is the unmapped-model miss.
    """
    return _UNMAPPED_MODEL_MARKER in str(exc)


def litellm_knows_model(litellm_model: str) -> bool:
    """Whether LiteLLM's static database carries an entry for *litellm_model*.

    Separates "LiteLLM says this model lacks a feature" from "LiteLLM has never
    heard of this model", which its own per-parameter query cannot: asked about
    an unknown id it answers with the ROUTE's generic parameter list, and that
    list is the same whatever the model is, so it carries no information about
    the model at all.

    Deliberately silent, unlike :func:`get_litellm_model_info`. A caller asking
    this is deciding whether some other answer counts as evidence, not reporting
    a miss, and the miss it would report is already logged once per call by the
    metadata lookup on the same model.

    Returns:
        Whether LiteLLM returned a non-empty metadata entry for the model.
    """
    try:
        return bool(_litellm.get_model_info(model=litellm_model))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- every miss LiteLLM reports for an unknown
        # model arrives as an exception, which is the answer rather than a
        # fault, and this must never fail a call.
        reraise_critical(exc)
        return False


#: LiteLLM prices per single token; provider config prices per 1,000 tokens.
_TOKENS_PER_PRICE_UNIT: int = 1000
#: Round back-filled per-1k costs to the budget precision (6 dp).
_COST_ROUNDING_DP: int = 6


def _coerce_cost_per_token(raw: object) -> float:
    """Coerce a LiteLLM per-token cost to a non-negative float.

    Returns:
        The cost as a float, or ``0.0`` when absent or non-numeric.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return 0.0
    try:
        value = float(raw)
    except ValueError, TypeError:
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(0.0, value)


def extract_model_pricing(info: Mapping[str, object]) -> tuple[float, float]:
    """Extract per-1k input/output costs from a LiteLLM info dict.

    LiteLLM stores ``input_cost_per_token`` / ``output_cost_per_token`` as a
    per-single-token price; the provider config prices per 1,000 tokens, so the
    values are scaled up.

    Returns:
        ``(cost_per_1k_input, cost_per_1k_output)``; ``(0.0, 0.0)`` when the
        info dict carries no usable pricing.
    """
    input_per_token = _coerce_cost_per_token(info.get("input_cost_per_token"))
    output_per_token = _coerce_cost_per_token(info.get("output_cost_per_token"))
    return (
        round(input_per_token * _TOKENS_PER_PRICE_UNIT, _COST_ROUNDING_DP),
        round(output_per_token * _TOKENS_PER_PRICE_UNIT, _COST_ROUNDING_DP),
    )


def register_operator_model_pricing(
    providers: Mapping[str, ProviderConfig],
) -> None:
    """Sync operator-supplied per-model prices into LiteLLM's model_cost DB.

    So a model the operator priced in config but that LiteLLM does not natively
    track (e.g. a gateway's aliased model id) resolves through
    ``get_model_info`` (used by capability enrichment and model refresh) with
    the operator's price rather than reporting no pricing. LiteLLM prices per
    single token, so the per-1k config costs are scaled down. Only LiteLLM-driver
    providers are registered (a scripted test provider is skipped). Best-effort:
    a LiteLLM failure never blocks provider construction.

    LiteLLM's ``model_cost`` is keyed by the bare model id (the same key
    capability enrichment queries with), so two providers configured with the
    same model id but different prices cannot both be represented; the last one
    wins and the conflict is logged rather than silently swallowed.
    """
    entries: dict[str, dict[str, object]] = {}
    for provider_name, config in providers.items():
        if config.driver != _LITELLM_DRIVER:
            continue
        litellm_provider = config.litellm_provider or provider_name
        for model in config.models:
            if model.cost_per_1k_input <= 0.0 and model.cost_per_1k_output <= 0.0:
                continue
            entry = {
                "input_cost_per_token": (
                    model.cost_per_1k_input / _TOKENS_PER_PRICE_UNIT
                ),
                "output_cost_per_token": (
                    model.cost_per_1k_output / _TOKENS_PER_PRICE_UNIT
                ),
                "litellm_provider": litellm_provider,
            }
            prior = entries.get(model.id)
            if prior is not None and prior != entry:
                logger.warning(
                    PROVIDER_MODEL_PRICING_CONFLICT,
                    model=model.id,
                    provider=provider_name,
                    reason="duplicate_model_id_differing_price",
                )
            entries[model.id] = entry
    if not entries:
        return
    try:
        _litellm.register_model(entries)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
            reason="register_model_failed",
            model_count=len(entries),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(PROVIDER_MODEL_PRICING_REGISTERED, model_count=len(entries))


def coerce_max_output_tokens(
    raw: object,
    *,
    fallback: int,
    litellm_model: str,
) -> int:
    """Coerce a LiteLLM ``max_output_tokens`` value to ``int``.

    Falls back to ``fallback`` (logging a warning) when the value is a
    bool, a non-numeric string, or any non-numeric type, rather than
    silently mis-reporting the context window or letting an unexpected
    type raise out of capability discovery.

    Returns:
        The coerced token count, or ``fallback`` when ``raw`` cannot be
        coerced.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        logger.warning(
            PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
            model=litellm_model,
            reason="max_output_tokens_unexpected_type",
            raw_type=type(raw).__name__,
        )
        return fallback
    try:
        return int(raw)
    except ValueError, TypeError:
        logger.warning(
            PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
            model=litellm_model,
            reason="max_output_tokens_not_coercible",
            raw_type=type(raw).__name__,
        )
        return fallback


def extract_model_metadata(
    info: Mapping[str, object],
    *,
    litellm_provider: str | None,
    model_id: str,
    parser: FamilyParser,
    base: ModelMetadata,
    source: MetadataSource = "litellm",
) -> ModelMetadata:
    """Build :class:`ModelMetadata` from a LiteLLM info dict and a parser.

    Single reader of the capability flags so the persisted config-layer
    metadata and the transient, per-request ``ModelCapabilities`` never
    drift. The output-token value is coerced through
    :func:`coerce_max_output_tokens` and dropped when absent or non-positive.

    Falls back to *base* **per field** rather than as a whole record: a
    LiteLLM card that is silent on one capability (a partial or missing
    entry) must not discard what a probe or an operator already established
    for the others, including the fields LiteLLM's static table never
    carries at all (``tool_calls_verified``, ``parameter_count``,
    ``cost_tier``), which this function always inherits unchanged from
    *base*.

    Args:
        info: A LiteLLM model-info / ``model_cost`` entry.
        litellm_provider: Provider hint for family-rule selection.
        model_id: The bare model id (no routing prefix).
        parser: Family/generation parser.
        base: The existing config-layer metadata to fall back to per field.
        source: Provenance stamped onto the result.

    Returns:
        A populated ``ModelMetadata``, per-field-supplemented from *base*.
    """
    identity = parser.parse(model_id, litellm_provider=litellm_provider)
    raw_max = info.get("max_output_tokens") or info.get("max_tokens")
    max_output = base.max_output_tokens
    if raw_max:
        coerced = coerce_max_output_tokens(raw_max, fallback=0, litellm_model=model_id)
        max_output = coerced if coerced > 0 else base.max_output_tokens
    return ModelMetadata(
        supports_tools=(
            bool(info["supports_function_calling"])
            if info.get("supports_function_calling") is not None
            else base.supports_tools
        ),
        tool_calls_verified=base.tool_calls_verified,
        supports_vision=(
            bool(info["supports_vision"])
            if info.get("supports_vision") is not None
            else base.supports_vision
        ),
        supports_reasoning=(
            bool(info["supports_reasoning"])
            if info.get("supports_reasoning") is not None
            else base.supports_reasoning
        ),
        supports_prompt_caching=(
            bool(info["supports_prompt_caching"])
            if info.get("supports_prompt_caching") is not None
            else base.supports_prompt_caching
        ),
        supports_embeddings=(
            str(info["mode"]) == "embedding"
            if info.get("mode") is not None
            else base.supports_embeddings
        ),
        supports_image_generation=(
            str(info["mode"]) == "image_generation"
            if info.get("mode") is not None
            else base.supports_image_generation
        ),
        max_output_tokens=max_output,
        parameter_count=base.parameter_count,
        cost_tier=base.cost_tier,
        family=identity.family,
        generation=identity.generation,
        release_date=identity.release_date,
        metadata_source=source,
    )
