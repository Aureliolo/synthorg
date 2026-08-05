"""Provider family lookup.

Maps a provider name to its family. The security evaluator reads it to
warn when the connection an operator chose for judging an agent shares
that agent's family, since a jailbreak of one family may also cover its
reviewer.
"""

from collections.abc import Mapping

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger

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
