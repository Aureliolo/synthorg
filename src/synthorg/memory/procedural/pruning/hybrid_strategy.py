"""Hybrid memory pruning strategy (TTL + Pareto).

Combines TTL-based expiry with Pareto frontier optimization for
robust memory management.
"""

from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry
from synthorg.memory.procedural.pruning.protocol import PruningStrategy
from synthorg.observability import get_logger

logger = get_logger(__name__)


class HybridPruningStrategy:
    """Hybrid pruning: TTL first, then Pareto on remainder.

    Applies TTL strategy to remove expired entries, then applies
    Pareto frontier filtering to remaining entries.

    Attributes:
        ttl_strategy: TTL pruning strategy instance.
        pareto_strategy: Pareto pruning strategy instance.
    """

    def __init__(
        self,
        ttl_strategy: PruningStrategy,
        pareto_strategy: PruningStrategy,
    ) -> None:
        """Initialize hybrid pruning strategy.

        Args:
            ttl_strategy: TTL strategy for expiry.
            pareto_strategy: Pareto strategy for frontier filtering.
        """
        self.ttl_strategy = ttl_strategy
        self.pareto_strategy = pareto_strategy

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "hybrid"

    async def prune(
        self,
        *,
        agent_id: NotBlankStr,
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[str, ...]:
        """Apply TTL then Pareto pruning.

        First removes expired entries via TTL, then applies Pareto
        frontier filtering to the remaining entries.

        Args:
            agent_id: Agent whose memories are being pruned.
            entries: Current procedural memory entries.

        Returns:
            Tuple of memory entry IDs to remove (from both strategies).
        """
        # Apply TTL first
        ttl_removals = await self.ttl_strategy.prune(
            agent_id=agent_id,
            entries=entries,
        )

        # Filter out TTL-marked entries for Pareto evaluation
        remaining_ids = {str(e.id) for e in entries} - set(ttl_removals)
        remaining_entries = tuple(e for e in entries if str(e.id) in remaining_ids)

        # Apply Pareto to remaining entries
        pareto_removals = await self.pareto_strategy.prune(
            agent_id=agent_id,
            entries=remaining_entries,
        )

        # Combine both removal lists (removing duplicates)
        all_removals = set(ttl_removals) | set(pareto_removals)
        return tuple(sorted(all_removals))
