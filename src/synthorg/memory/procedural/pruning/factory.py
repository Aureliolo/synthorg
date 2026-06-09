"""Factory for building pruning strategies from configuration."""

from synthorg.core.registry import StrategyRegistry
from synthorg.memory.procedural.pruning.config import PruningConfig
from synthorg.memory.procedural.pruning.hybrid_strategy import (
    HybridPruningStrategy,
)
from synthorg.memory.procedural.pruning.pareto_strategy import (
    ParetoPruningStrategy,
)
from synthorg.memory.procedural.pruning.protocol import PruningStrategy
from synthorg.memory.procedural.pruning.ttl_strategy import TtlPruningStrategy
from synthorg.observability import get_logger

logger = get_logger(__name__)


def _build_ttl(config: PruningConfig) -> PruningStrategy:
    """Registry entry: build the TTL (age-based) pruning strategy.

    Returns:
        A ``TtlPruningStrategy`` that evicts entries older than ``max_age_days``.
    """
    return TtlPruningStrategy(max_age_days=config.max_age_days)


def _build_pareto(config: PruningConfig) -> PruningStrategy:
    """Registry entry: build the Pareto (count-capped) pruning strategy.

    Returns:
        A ``ParetoPruningStrategy`` that caps retained entries at ``max_entries``.
    """
    return ParetoPruningStrategy(max_entries=config.max_entries)


def _build_hybrid(config: PruningConfig) -> PruningStrategy:
    """Registry entry: build the hybrid (TTL + Pareto) pruning strategy.

    Returns:
        A ``HybridPruningStrategy`` combining the age and count caps.
    """
    return HybridPruningStrategy(
        ttl_strategy=TtlPruningStrategy(max_age_days=config.max_age_days),
        pareto_strategy=ParetoPruningStrategy(max_entries=config.max_entries),
    )


_REGISTRY: StrategyRegistry[PruningStrategy] = StrategyRegistry(
    {"ttl": _build_ttl, "pareto": _build_pareto, "hybrid": _build_hybrid},
    kind="pruning",
)


def build_pruning_strategy(config: PruningConfig) -> PruningStrategy:
    """Build a pruning strategy from configuration.

    Args:
        config: Pruning strategy configuration.

    Returns:
        Configured pruning strategy instance.

    Raises:
        StrategyFactoryNotFoundError: If ``config.type`` is not registered.
    """
    return _REGISTRY.build(config.type, config)
