# module-kind: declarative
"""How an agent's own ``(provider, model)`` pair reaches a driver."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.resolver import ModelResolver

if TYPE_CHECKING:
    # A cycle breaker: the root config names the coordination package,
    # which runs sub-agents on an ``AgentEngine``.
    from synthorg.config.schema import ProviderConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineRouting:
    """The registry a dispatch resolves an agent's bound pair against.

    Attributes:
        provider_registry: Where ``identity.model.provider`` is looked up.
            ``None`` leaves every dispatch on :attr:`EngineCore.provider`,
            which attributes an agent's spend to a connection it may not
            run on; a wired registry that does not know the provider fails
            closed instead.
        provider_configs: Per-connection configuration the LLM-backed
            security features read, or ``None``.
        model_resolver: Routing-table lookups for those same features, or
            ``None``.
    """

    provider_registry: ProviderRegistry | None
    provider_configs: Mapping[str, ProviderConfig] | None
    model_resolver: ModelResolver | None


__all__ = ["EngineRouting"]
