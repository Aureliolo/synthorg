"""Provider family lookup.

Answers which family a model belongs to, and whether two of them are close
enough that one cannot independently judge the other. The security evaluator
reads it to warn when the connection an operator chose for judging an agent
shares that agent's family, since a jailbreak of one family may also cover its
reviewer.

A family is a property of the ORGANISATION that trained a model, never of the
connection that serves it. An aggregating provider reaches many organisations
through one endpoint, and one organisation can be reached through several
connections, so deriving a family from the provider name is wrong in both
directions: it reads a decorrelated pair as correlated and a correlated pair as
decorrelated.
"""

from collections.abc import Mapping
from typing import Final

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger

logger = get_logger(__name__)

#: Variant segments a composed family label may end with. A variant names what
#: a model was tuned FOR, not who trained it, so two labels differing only here
#: descend from one lineage and cannot judge each other independently.
_VARIANT_SUFFIXES: Final[tuple[str, ...]] = (
    "code",
    "coder",
    "chat",
    "instruct",
    "embed",
    "embedding",
    "reasoning",
    "thinking",
    "vision",
)


def get_family(
    provider_name: str,
    configs: Mapping[str, ProviderConfig],
    model_id: str | None = None,
) -> str:
    """Return the family for a provider, or for one model reached through it.

    The MODEL's family wins wherever one is known, because a connection does
    not identify a family. Falls back to the connection's declared family, then
    to the provider name.

    Args:
        provider_name: Registered provider name.
        configs: Provider config dict (key = provider name).
        model_id: Optional model id or alias reached through that provider.

    Returns:
        The family string.
    """
    config = configs.get(provider_name)
    if config is None:
        return provider_name
    if model_id is not None:
        for model in config.models:
            if model_id in {model.id, model.alias} and model.metadata.family:
                return model.metadata.family
    if config.family is not None:
        return config.family
    return provider_name


def shares_lineage(left: str, right: str) -> bool:
    """Whether two families are close enough that neither can judge the other.

    Compared on the BASE family, because a variant suffix splits one
    organisation's models into several labels: a code variant and a chat
    variant of the same base model share training lineage, so a judge drawn
    from one is not independent of the other, and reading them as different
    families claims a decorrelation nobody has. The split itself is wanted
    elsewhere (an upgrade recommender must not offer a chat model as the
    upgrade to a coder), so it is narrowed here rather than removed there.

    Args:
        left: One family label.
        right: The other.

    Returns:
        ``True`` when both descend from the same base family.
    """
    return _base_family(left) == _base_family(right)


def _base_family(family: str) -> str:
    """Strip a trailing variant segment from a composed family label.

    Returns:
        The base family (``qwen-coder`` -> ``qwen``), or *family* lowercased
        and otherwise unchanged when it carries no known variant suffix.
    """
    base = family.lower()
    for variant in _VARIANT_SUFFIXES:
        suffix = f"-{variant}"
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base
