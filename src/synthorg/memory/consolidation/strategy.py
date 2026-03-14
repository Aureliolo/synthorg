"""Consolidation strategy protocol.

Defines the interface for memory consolidation algorithms that
compress and summarize older memories.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.models import ConsolidationResult  # noqa: TC001
from synthorg.memory.models import MemoryEntry  # noqa: TC001


@runtime_checkable
class ConsolidationStrategy(Protocol):
    """Protocol for memory consolidation strategies.

    Implementations receive a batch of memory entries and produce
    a ``ConsolidationResult`` indicating which entries were merged,
    removed, or summarized.
    """

    async def consolidate(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Consolidate a batch of memory entries.

        Args:
            entries: Memory entries to consolidate.
            agent_id: Owning agent identifier.

        Returns:
            Result describing what was consolidated.
        """
        ...
