"""MemoryBackend protocol — lifecycle + memory operations.

Application code depends on this protocol for agent memory storage
and retrieval.  Concrete backends implement this protocol to provide
connection management, health monitoring, and memory CRUD operations.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.models import (
    MemoryEntry,  # noqa: TC001
    MemoryQuery,  # noqa: TC001
    MemoryStoreRequest,  # noqa: TC001
)


@runtime_checkable
class MemoryBackend(Protocol):
    """Structural interface for agent memory storage backends.

    Concrete backends implement this protocol to provide per-agent
    memory storage, retrieval, and lifecycle management.

    All CRUD operations (``store``, ``retrieve``, ``get``, ``delete``,
    ``count``) require a connected backend and raise
    ``MemoryConnectionError`` if called before ``connect()``.

    Attributes:
        is_connected: Whether the backend has an active connection.
        backend_name: Human-readable backend identifier.
    """

    async def connect(self) -> None:
        """Establish connection to the memory backend.

        Raises:
            MemoryConnectionError: If the connection cannot be
                established.
        """
        ...

    async def disconnect(self) -> None:
        """Close the memory backend connection.

        Safe to call even if not connected.
        """
        ...

    async def health_check(self) -> bool:
        """Check whether the backend is healthy and responsive.

        Returns:
            ``True`` if the backend is reachable and operational,
            ``False`` if unreachable or unhealthy.

        Note:
            Implementations should catch connection-level errors,
            log them at WARNING level with full exception context,
            and return ``False``.  The caught exception must never be
            silently discarded.  Only raise for programming errors
            (e.g. backend not initialized).
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        ...

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier (e.g. ``"mem0"``)."""
        ...

    async def store(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Store a memory entry for an agent.

        Args:
            agent_id: Owning agent identifier.
            request: Memory content and metadata.

        Returns:
            The backend-assigned memory ID.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the store operation fails.
        """
        ...

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories for an agent, ordered by relevance.

        When ``query.text`` is ``None``, performs metadata-only
        filtering (no semantic search).

        Args:
            agent_id: Owning agent identifier.
            query: Retrieval parameters.

        Returns:
            Matching memory entries ordered by relevance.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the retrieval fails.
        """
        ...

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> MemoryEntry | None:
        """Get a specific memory entry by ID.

        Returns ``None`` when the entry does not exist —
        ``MemoryNotFoundError`` is never raised by this method.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            The memory entry, or ``None`` if not found.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the backend query fails.
        """
        ...

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Delete a specific memory entry.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            ``True`` if the entry was deleted, ``False`` if not found.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the delete operation fails.
        """
        ...

    async def count(
        self,
        agent_id: NotBlankStr,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count memory entries for an agent.

        Args:
            agent_id: Owning agent identifier.
            category: Optional category filter.

        Returns:
            Number of matching entries.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the count query fails.
        """
        ...
