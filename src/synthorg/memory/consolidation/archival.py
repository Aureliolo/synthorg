"""Archival store protocol for long-term memory storage.

Defines the protocol for moving memories from the hot store into
cold (archival) storage, with search and restore capabilities.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.models import ArchivalEntry  # noqa: TC001
from synthorg.memory.models import MemoryQuery  # noqa: TC001


@runtime_checkable
class ArchivalStore(Protocol):
    """Protocol for long-term memory archival storage.

    Concrete implementations handle moving memories from the hot
    (active) store into cold storage for long-term preservation.
    """

    async def archive(self, entry: ArchivalEntry) -> NotBlankStr:
        """Archive a memory entry.

        Args:
            entry: The archival entry to store.

        Returns:
            The assigned archive entry ID.
        """
        ...

    async def search(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[ArchivalEntry, ...]:
        """Search archived entries for a specific agent.

        Args:
            agent_id: Agent whose archived entries to search.
            query: Search parameters.

        Returns:
            Matching archived entries owned by the agent.
        """
        ...

    async def restore(
        self,
        agent_id: NotBlankStr,
        entry_id: NotBlankStr,
    ) -> ArchivalEntry | None:
        """Restore a specific archived entry for a specific agent.

        Args:
            agent_id: Agent who owns the archived entry.
            entry_id: The archive entry ID.

        Returns:
            The archived entry, or ``None`` if not found or not
            owned by the agent.
        """
        ...

    async def count(self, agent_id: NotBlankStr) -> int:
        """Count archived entries for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Number of archived entries.
        """
        ...
