# module-kind: code
"""Shared model-selection helper for the LLM security evaluators.

The LLM-backed security evaluator and the safety classifier resolve an
evaluation model identically: an explicit override wins, otherwise the
selected provider's first configured model, otherwise the provider name
as a last-resort hint. Only the warning event differs, so it is supplied
by the caller.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    # ``config.schema`` imports up through ``api.config`` into the
    # ``security`` package, so a runtime import here would close a cycle
    # via ``security.safety_classifier``. The annotation is type-only.
    from synthorg.config.schema import ProviderConfig

logger = get_logger(__name__)


def select_security_eval_model(
    model: str | None,
    configs: Mapping[str, ProviderConfig],
    provider_name: str,
    *,
    event: str,
) -> str:
    """Resolve the model id for a security evaluation against a provider.

    Args:
        model: Explicit model override from the evaluator config, if any.
        configs: Provider configs keyed by provider name.
        provider_name: The selected provider to resolve a model for.
        event: Warning event to emit when no model is configured.

    Returns:
        The model alias or id; falls back to ``provider_name`` when no
        model is configured (likely to fail at the driver level, where
        the error policy handles it).
    """
    if model is not None:
        return model

    config = configs.get(provider_name)
    if config is not None and config.models:
        first = config.models[0]
        return first.alias or first.id

    logger.warning(
        event,
        note=(
            f"No model configured for provider {provider_name!r}, "
            "using provider name as model hint"
        ),
        provider_name=provider_name,
    )
    return provider_name
