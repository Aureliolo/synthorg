"""Provider family utilities for cross-family model selection.

Maps provider names to families and supports querying providers
by family exclusion -- used by the LLM security evaluator to
select a model from a different provider family than the agent.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    # config.schema -> api.config -> ... -> security.safety_classifier
    # re-imports this module; a runtime import forms a cycle.
    from synthorg.config.schema import ProviderConfig

logger = get_logger(__name__)


def get_family(
    provider_name: str,
    configs: Mapping[str, ProviderConfig],
) -> str:
    """Return the family for a provider.

    If the provider has an explicit ``family`` field, return it.
    Otherwise, fall back to the provider name itself.

    Args:
        provider_name: Registered provider name.
        configs: Provider config dict (key = provider name).

    Returns:
        The provider's family string.
    """
    config = configs.get(provider_name)
    if config is not None and config.family is not None:
        return config.family
    return provider_name


def providers_excluding_family(
    family: str,
    configs: Mapping[str, ProviderConfig],
) -> tuple[str, ...]:
    """Return provider names whose family differs from *family*.

    Args:
        family: The family to exclude.
        configs: Provider config dict (key = provider name).

    Returns:
        Sorted tuple of provider names from other families.
    """
    return tuple(
        sorted(name for name in configs if get_family(name, configs) != family)
    )
