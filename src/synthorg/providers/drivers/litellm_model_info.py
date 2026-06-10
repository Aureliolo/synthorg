# module-kind: code
"""LiteLLM static model-metadata lookup and field coercion.

Isolates the ``litellm.get_model_info`` query and the defensive
coercion of its loosely-typed fields so ``litellm_driver`` stays
focused on completion dispatch and response mapping.
"""

import litellm as _litellm

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_INFO_UNAVAILABLE,
    PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
)

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
