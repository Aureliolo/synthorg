# module-kind: code
"""LiteLLM static model-metadata lookup and field coercion.

Isolates the ``litellm.get_model_info`` query and the defensive
coercion of its loosely-typed fields so ``litellm_driver`` stays
focused on completion dispatch and response mapping.
"""

from collections.abc import Mapping

import litellm as _litellm

from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_INFO_UNAVAILABLE,
    PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
)
from synthorg.providers.family_parser import FamilyParser

logger = get_logger(__name__)


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
        logger.warning(
            PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
            model=litellm_model,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}
    return info if isinstance(info, dict) else {}


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
    source: MetadataSource = "litellm",
) -> ModelMetadata:
    """Build :class:`ModelMetadata` from a LiteLLM info dict and a parser.

    Single reader of the capability flags so the persisted config-layer
    metadata and the routing-layer ``ModelCapabilities`` never drift.
    The output-token value is coerced through :func:`coerce_max_output_tokens`
    and dropped (``None``) when absent or non-positive.

    Args:
        info: A LiteLLM model-info / ``model_cost`` entry.
        litellm_provider: Provider hint for family-rule selection.
        model_id: The bare model id (no routing prefix).
        parser: Family/generation parser.
        source: Provenance stamped onto the result.

    Returns:
        A populated ``ModelMetadata`` (safe defaults for missing keys).
    """
    identity = parser.parse(model_id, litellm_provider=litellm_provider)
    raw_max = info.get("max_output_tokens") or info.get("max_tokens")
    max_output: int | None = None
    if raw_max:
        coerced = coerce_max_output_tokens(raw_max, fallback=0, litellm_model=model_id)
        max_output = coerced if coerced > 0 else None
    return ModelMetadata(
        supports_tools=bool(info.get("supports_function_calling", False)),
        supports_vision=bool(info.get("supports_vision", False)),
        supports_reasoning=bool(info.get("supports_reasoning", False)),
        max_output_tokens=max_output,
        family=identity.family,
        generation=identity.generation,
        release_date=identity.release_date,
        metadata_source=source,
    )
