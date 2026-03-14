"""SharedKnowledgeStore protocol — cross-agent memory.

Backends that support cross-agent shared knowledge implement this
protocol in addition to ``MemoryBackend``.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.models import (
    MemoryEntry,  # noqa: TC001
    MemoryQuery,  # noqa: TC001
    MemoryStoreRequest,  # noqa: TC001
)


@runtime_checkable
class SharedKnowledgeStore(Protocol):
    """Cross-agent shared knowledge operations.

    Backends that support shared memory implement this protocol
    alongside ``MemoryBackend``.  Not all backends need cross-agent
    queries — this keeps the base protocol clean.
    """

    async def publish(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Publish a memory to the shared knowledge store.

        Args:
            agent_id: Publishing agent identifier.
            request: Memory content and metadata.

        Returns:
            The backend-assigned shared memory ID.

        Raises:
            MemoryStoreError: If the publish operation fails.
        """
        ...

    async def search_shared(
        self,
        query: MemoryQuery,
        *,
        exclude_agent: NotBlankStr | None = None,
    ) -> tuple[MemoryEntry, ...]:
        """Search the shared knowledge store across agents.

        Args:
            query: Search parameters.
            exclude_agent: Optional agent ID to exclude from results.

        Returns:
            Matching shared memory entries ordered by relevance.

        Raises:
            MemoryRetrievalError: If the search fails.
        """
        ...

    async def retract(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Remove a memory from the shared knowledge store.

        Args:
            agent_id: Retracting agent identifier.
            memory_id: Shared memory identifier.

        Returns:
            ``True`` if retracted, ``False`` if not found.

        Raises:
            MemoryStoreError: If the retraction operation fails.
        """
        ...
