"""Trust strategy factory.

Dispatches :class:`TrustConfig.strategy` to the matching
:class:`TrustStrategy` implementation. ``DISABLED`` returns ``None`` so
the caller can skip constructing a :class:`TrustService` entirely
(conditional instantiation in degenerate configs).
"""

from synthorg.core.registry.strategy import StrategyRegistry
from synthorg.security.trust.config import TrustConfig
from synthorg.security.trust.enums import TrustStrategyType
from synthorg.security.trust.milestone_strategy import MilestoneTrustStrategy
from synthorg.security.trust.per_category_strategy import PerCategoryTrustStrategy
from synthorg.security.trust.protocol import TrustStrategy
from synthorg.security.trust.weighted_strategy import WeightedTrustStrategy

_REGISTRY: StrategyRegistry[TrustStrategy] = StrategyRegistry(
    {
        TrustStrategyType.WEIGHTED.value: WeightedTrustStrategy,
        TrustStrategyType.PER_CATEGORY.value: PerCategoryTrustStrategy,
        TrustStrategyType.MILESTONE.value: MilestoneTrustStrategy,
    },
    kind="trust_strategy",
)


def build_trust_strategy(config: TrustConfig) -> TrustStrategy | None:
    """Construct a :class:`TrustStrategy` from configuration.

    Args:
        config: Trust configuration.

    Returns:
        The configured strategy, or ``None`` when ``config.strategy``
        is :attr:`TrustStrategyType.DISABLED`. Callers treat ``None``
        as "do not build a :class:`TrustService` at all" so disabled
        trust skips the orchestrator construction entirely.

    Raises:
        StrategyFactoryNotFoundError: ``config.strategy`` is not a
            registered non-disabled strategy.
    """
    if config.strategy is TrustStrategyType.DISABLED:
        return None
    return _REGISTRY.build(config.strategy.value, config=config)
