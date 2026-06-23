# module-kind: code
"""LiteLLM model-cost parsing for provider management.

Reads LiteLLM's built-in ``model_cost`` database into typed
``ProviderModelConfig`` entries (pricing + context), preferring shorter
model identifiers over dated variants. This module owns the litellm
parsing concern alone, kept distinct from config transforms and
discovery auth.
"""

import re
from typing import Final

from pydantic import JsonValue

from synthorg.config.schema import ProviderModelConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_LITELLM_LOOKUP_SKIPPED,
    PROVIDER_LITELLM_MODELS_EMPTY,
    PROVIDER_LITELLM_MODELS_LOADED,
)

logger = get_logger(__name__)

# Date suffix pattern for model names (e.g. "-YYYYMMDD" like "-20250514")
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_DEFAULT_MAX_CONTEXT: Final[int] = 200_000


def _coerce_cost(value: JsonValue) -> float:
    """Coerce a litellm per-token cost to ``float``.

    Returns:
        The value as a ``float``.

    Raises:
        TypeError: If *value* is not a real number (``bool`` is rejected
            too). The caller's ``except (TypeError, ValueError)`` turns
            this into a skipped, logged ``malformed_model_entry``.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"non-numeric cost value: {type(value).__name__}"
        raise TypeError(msg)
    return float(value)


def _parse_litellm_entry(
    model_name: str,
    info: dict[str, JsonValue],
    litellm_provider: str,
    version_filter: re.Pattern[str] | None,
) -> tuple[str, ProviderModelConfig] | None:
    """Parse a single litellm.model_cost entry into a model config.

    Args:
        model_name: Raw model key from litellm.model_cost.
        info: Model metadata dict.
        litellm_provider: Provider identifier for prefix stripping.
        version_filter: Optional regex; entry is skipped when it
            does not match.

    Returns:
        ``(base_name, config)`` tuple, or ``None`` if the entry
        should be skipped (wrong provider, filtered, malformed).
    """
    if info.get("litellm_provider") != litellm_provider:
        return None

    # Strip provider prefix if present (e.g. "provider/model-name")
    model_id = model_name.removeprefix(f"{litellm_provider}/")

    if version_filter and not version_filter.search(model_id):
        return None

    base_name = _DATE_SUFFIX_RE.sub("", model_id)
    input_cost = info.get("input_cost_per_token") or 0
    output_cost = info.get("output_cost_per_token") or 0
    max_input = info.get("max_input_tokens", _DEFAULT_MAX_CONTEXT)

    try:
        config = ProviderModelConfig(
            id=model_id,
            cost_per_1k_input=round(_coerce_cost(input_cost) * 1000, 6),
            cost_per_1k_output=round(_coerce_cost(output_cost) * 1000, 6),
            max_context=(
                max_input if isinstance(max_input, int) else _DEFAULT_MAX_CONTEXT
            ),
        )
    except (TypeError, ValueError) as exc:
        # WARNING (not DEBUG) so an upstream LiteLLM data-shape change
        # that suddenly invalidates a provider's model surface is
        # visible to operators -- the alternative is a silent "no
        # models loaded" outcome at line ~413 that is indistinguishable
        # from a provider that genuinely has zero models.
        logger.warning(
            PROVIDER_LITELLM_LOOKUP_SKIPPED,
            reason="malformed_model_entry",
            model=model_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    return (base_name, config)


def models_from_litellm(
    litellm_provider: str,
) -> tuple[ProviderModelConfig, ...]:
    """Query litellm.model_cost for all models of a given provider.

    Returns model configs populated with pricing and context data
    from LiteLLM's built-in model database. Prefers shorter model
    identifiers over dated variants (e.g. ``example-large-001``
    over ``example-large-001-20260205``).

    Provider-specific model generation filters (defined in
    ``presets.MODEL_VERSION_FILTERS``) exclude older models.

    Args:
        litellm_provider: LiteLLM provider identifier
            (e.g. ``"example-provider"``).

    Returns:
        Tuple of model configs, or empty tuple if litellm is not
        installed or no models match.
    """
    try:
        import litellm  # noqa: PLC0415
    except ImportError:
        logger.warning(
            PROVIDER_LITELLM_LOOKUP_SKIPPED,
            reason="litellm_not_installed",
            provider=litellm_provider,
        )
        return ()

    from synthorg.providers.drivers.litellm_model_info import (  # noqa: PLC0415
        extract_model_metadata,
    )
    from synthorg.providers.family_parser import get_family_parser  # noqa: PLC0415
    from synthorg.providers.presets import MODEL_VERSION_FILTERS  # noqa: PLC0415

    parser = get_family_parser()
    version_filter = MODEL_VERSION_FILTERS.get(litellm_provider)
    seen: dict[str, ProviderModelConfig] = {}

    for model_name, info in litellm.model_cost.items():
        if not isinstance(info, dict):
            continue
        parsed = _parse_litellm_entry(
            model_name,
            info,
            litellm_provider,
            version_filter,
        )
        if parsed is None:
            continue
        base_name, config = parsed
        config = config.model_copy(
            update={
                "metadata": extract_model_metadata(
                    info,
                    litellm_provider=litellm_provider,
                    model_id=config.id,
                    parser=parser,
                ),
            },
        )
        existing = seen.get(base_name)
        if existing is not None and len(existing.id) <= len(config.id):
            continue
        seen[base_name] = config

    result = tuple(sorted(seen.values(), key=lambda m: m.id))
    if result:
        logger.info(
            PROVIDER_LITELLM_MODELS_LOADED,
            provider=litellm_provider,
            count=len(result),
        )
    else:
        logger.info(
            PROVIDER_LITELLM_MODELS_EMPTY,
            provider=litellm_provider,
            version_filter_applied=version_filter is not None,
        )
    return result
