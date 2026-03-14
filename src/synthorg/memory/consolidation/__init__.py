"""Memory consolidation — strategies, retention, archival, and service.

Re-exports the public API so consumers can import from
``synthorg.memory.consolidation`` directly.
"""

from synthorg.memory.consolidation.archival import ArchivalStore
from synthorg.memory.consolidation.config import (
    ArchivalConfig,
    ConsolidationConfig,
    RetentionConfig,
)
from synthorg.memory.consolidation.models import (
    ArchivalEntry,
    ConsolidationResult,
    RetentionRule,
)
from synthorg.memory.consolidation.retention import RetentionEnforcer
from synthorg.memory.consolidation.service import MemoryConsolidationService
from synthorg.memory.consolidation.simple_strategy import (
    SimpleConsolidationStrategy,
)
from synthorg.memory.consolidation.strategy import ConsolidationStrategy

__all__ = [
    "ArchivalConfig",
    "ArchivalEntry",
    "ArchivalStore",
    "ConsolidationConfig",
    "ConsolidationResult",
    "ConsolidationStrategy",
    "MemoryConsolidationService",
    "RetentionConfig",
    "RetentionEnforcer",
    "RetentionRule",
    "SimpleConsolidationStrategy",
]
