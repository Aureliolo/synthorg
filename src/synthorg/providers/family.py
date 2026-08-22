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

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_FAMILY_UNDECLARED

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
        return _undeclared(provider_name, model_id, reason="no_such_connection")
    if model_id is not None:
        model = model_named(config, model_id)
        if model is not None and model.metadata.family:
            return model.metadata.family
    if config.family is not None:
        return config.family
    return _undeclared(provider_name, model_id, reason="family_not_declared")


def model_named(config: ProviderConfig, model_id: str) -> ProviderModelConfig | None:
    """Find the model *model_id* names on *config*, by id or by alias.

    One lookup because an alias and an id name the same model, and a caller
    that checks only one of them silently answers "not this connection's" for
    every pair written the other way.

    Args:
        config: The connection to search.
        model_id: A model id or an alias reached through it.

    Returns:
        The model, or ``None`` when the connection serves no such name.
    """
    for model in config.models:
        if model_id in {model.id, model.alias}:
            return model
    return None


def _undeclared(provider_name: str, model_id: str | None, *, reason: str) -> str:
    """Answer the connection name where nothing declared a family.

    Args:
        provider_name: The connection asked about.
        model_id: The model asked about, when one was named.
        reason: What ran out, for the log.

    Returns:
        *provider_name*, which is the only thing left to answer.
    """
    logger.debug(
        PROVIDER_FAMILY_UNDECLARED,
        provider=provider_name,
        model_id=model_id,
        reason=reason,
    )
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
        The base family (``example-family-coder`` -> ``example-family``), or
        *family* lowercased and otherwise unchanged when it carries no known
        variant suffix.
    """
    base = family.lower()
    for variant in _VARIANT_SUFFIXES:
        suffix = f"-{variant}"
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base
