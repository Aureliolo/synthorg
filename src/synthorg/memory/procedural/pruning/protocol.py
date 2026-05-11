"""Protocol for procedural memory pruning strategies.

Defines the interface for pluggable pruning strategies that
determine which procedural memory entries should be removed
to maintain a manageable and high-quality memory store.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.models import MemoryEntry  # noqa: TC001


# Pluggable pruning seam: pruning/factory.py dispatches across 3 impls
# (TTL/Pareto/Hybrid) with HybridPruningStrategy composition.
@runtime_checkable
class PruningStrategy(Protocol):
    """Strategy for pruning procedural memory entries.

    Implementations include TTL-based expiry, Pareto frontier
    pruning, and hybrid combinations.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def prune(
        self,
        *,
        agent_id: NotBlankStr,
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[str, ...]:
        """Identify entries to remove.

        Args:
            agent_id: Agent whose memories are being pruned.
            entries: Current procedural memory entries.

        Returns:
            Tuple of memory entry IDs to remove.
        """
        ...
