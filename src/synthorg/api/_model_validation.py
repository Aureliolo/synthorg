"""Shared provider/model catalog validation.

A single source of truth for "this provider exists and exposes this
model id", reused by the first-run setup controller and the post-setup
agent-mutation path so both reject a model assignment that points at a
provider/model the live catalogue no longer contains.
"""

from collections.abc import Mapping

from synthorg.config.schema import ProviderConfig
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_MODEL_NOT_FOUND,
    SETUP_PROVIDER_NOT_FOUND,
)

logger = get_logger(__name__)


def validate_provider_model_pair(
    providers: Mapping[str, ProviderConfig],
    provider_name: str,
    model_id: str,
) -> None:
    """Validate that a provider exists and exposes the given model.

    Args:
        providers: Provider name -> config mapping.
        provider_name: Provider to look up.
        model_id: Model identifier to find within the provider.

    Raises:
        NotFoundError: If the provider does not exist.
        ValidationError: If the model is not in the provider.
    """
    if provider_name not in providers:
        msg = f"Provider {provider_name!r} not found"
        logger.warning(SETUP_PROVIDER_NOT_FOUND, provider=provider_name)
        raise NotFoundError(msg)

    known_ids = {m.id for m in providers[provider_name].models}
    if model_id not in known_ids:
        msg = f"Model {model_id!r} not found in provider {provider_name!r}"
        logger.warning(SETUP_MODEL_NOT_FOUND, provider=provider_name, model=model_id)
        raise ValidationError(msg)


__all__ = ["validate_provider_model_pair"]
