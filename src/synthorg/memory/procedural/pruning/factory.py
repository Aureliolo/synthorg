"""Factory for building pruning strategies from configuration."""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.memory.procedural.pruning.hybrid_strategy import (
    HybridPruningStrategy,
)
from synthorg.memory.procedural.pruning.pareto_strategy import (
    ParetoPruningStrategy,
)
from synthorg.memory.procedural.pruning.ttl_strategy import TtlPruningStrategy

if TYPE_CHECKING:
    from synthorg.memory.procedural.pruning.config import PruningConfig
    from synthorg.memory.procedural.pruning.protocol import PruningStrategy


def _build_ttl(config: PruningConfig) -> PruningStrategy:
    return TtlPruningStrategy(max_age_days=config.max_age_days)


def _build_pareto(config: PruningConfig) -> PruningStrategy:
    return ParetoPruningStrategy(max_entries=config.max_entries)


def _build_hybrid(config: PruningConfig) -> PruningStrategy:
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
