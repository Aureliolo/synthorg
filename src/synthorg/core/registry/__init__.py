"""Generic registry primitives.

Domain-specific registries (PersistenceBackendRegistry, MemoryBackendRegistry)
live in their respective subsystems; this package only exports the generic
:class:`StrategyRegistry` used for protocol+strategy+factory dispatch on a
config discriminator.
"""

from synthorg.core.registry.errors import (
    StrategyFactoryError,
    StrategyFactoryNotFoundError,
)
from synthorg.core.registry.strategy import StrategyRegistry

__all__ = [
    "StrategyFactoryError",
    "StrategyFactoryNotFoundError",
    "StrategyRegistry",
]
