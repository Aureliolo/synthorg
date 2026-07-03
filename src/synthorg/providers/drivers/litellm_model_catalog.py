"""Model-catalogue lookup for :class:`LiteLLMDriver`.

Sibling of ``litellm_driver.py``: builds the id/alias -> config lookup
from a provider's configured model list, resolves a request-time model
string against it, and answers the ``serves_model`` membership check
the provider registry uses for model-aware provider selection.
"""

from collections.abc import Mapping

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_CALL_ERROR,
    PROVIDER_MODEL_NOT_FOUND,
)
from synthorg.providers import errors

logger = get_logger(__name__)


def build_model_lookup(
    models: tuple[ProviderModelConfig, ...],
) -> dict[str, ProviderModelConfig]:
    """Build alias/id -> model config lookup.

    Returns:
        A dict mapping each model's canonical ID and any alias to its
        ``ProviderModelConfig``.

    Raises:
        ValueError: If two models share the same ID, or an alias
            collides with another model's ID or alias.
    """
    lookup: dict[str, ProviderModelConfig] = {}
    for m in models:
        if m.id in lookup and lookup[m.id] is not m:
            logger.error(
                PROVIDER_CALL_ERROR,
                error="duplicate_model_id",
                model_id=m.id,
            )
            msg = f"Duplicate model lookup key: {m.id!r}"
            raise ValueError(msg)
        lookup[m.id] = m
        if m.alias is not None:
            if m.alias in lookup and lookup[m.alias].id != m.id:
                logger.error(
                    PROVIDER_CALL_ERROR,
                    error="model_alias_collision",
                    alias=m.alias,
                    collides_with=lookup[m.alias].id,
                )
                msg = (
                    f"Model alias {m.alias!r} collides with "
                    f"existing key for model {lookup[m.alias].id!r}"
                )
                raise ValueError(msg)
            lookup[m.alias] = m
    return lookup


def resolve_model(
    model_lookup: Mapping[str, ProviderModelConfig],
    model: str,
    *,
    provider_name: str,
) -> ProviderModelConfig:
    """Resolve a model alias or ID to its config.

    Returns:
        The ``ProviderModelConfig`` for the requested model alias or ID.

    Raises:
        ModelNotFoundError: If not found in this provider.
    """
    config = model_lookup.get(model)
    if config is None:
        logger.error(
            PROVIDER_MODEL_NOT_FOUND,
            provider=provider_name,
            model=model,
            available=sorted(model_lookup),
        )
        msg = f"Model {model!r} not found in provider {provider_name!r}"
        raise errors.ModelNotFoundError(
            msg,
            context={"provider": provider_name, "model": model},
        )
    return config


def model_is_known(model_lookup: Mapping[str, ProviderModelConfig], model: str) -> bool:
    """Membership check over the driver's configured model catalogue.

    Returns:
        ``True`` when *model* resolves as an id or alias in the lookup.
    """
    return model in model_lookup
