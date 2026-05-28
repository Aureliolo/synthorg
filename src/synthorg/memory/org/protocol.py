"""OrgMemoryBackend protocol -- lifecycle + org memory operations.

Application code depends on this protocol for shared organizational
memory storage and retrieval.  Concrete backends implement this
protocol to provide company-wide knowledge management.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.memory.org.models import (
    OrgFact,
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)


@runtime_checkable
class OrgMemoryBackend(Protocol):
    """Structural interface for organizational memory backends.

    Provides company-wide knowledge storage, retrieval, and lifecycle
    management.  All operations require a connected backend.

    Attributes:
        is_connected: Whether the backend has an active connection.
        backend_name: Human-readable backend identifier.
    """

    async def connect(self) -> None:
        """Establish connection to the org memory backend.

        Raises:
            OrgMemoryConnectionError: If the connection fails.
        """
        ...

    async def disconnect(self) -> None:
        """Close the org memory backend connection.

        Safe to call even if not connected.
        """
        ...

    async def health_check(self) -> bool:
        """Check whether the backend is healthy and responsive.

        Returns:
            ``True`` if the backend is reachable and operational.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        ...

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        ...

    async def query(self, query: OrgMemoryQuery) -> tuple[OrgFact, ...]:
        """Query organizational facts.

        Args:
            query: Query parameters.

        Returns:
            Matching facts ordered by relevance.

        Raises:
            OrgMemoryConnectionError: If not connected.
            OrgMemoryQueryError: If the query fails.
        """
        ...

    async def write(
        self,
        request: OrgFactWriteRequest,
        *,
        author: OrgFactAuthor,
    ) -> NotBlankStr:
        """Write a new organizational fact.

        Args:
            request: Fact content and category.
            author: The author of the fact.

        Returns:
            The assigned fact ID.

        Raises:
            OrgMemoryConnectionError: If not connected.
            OrgMemoryAccessDeniedError: If write access is denied.
            OrgMemoryWriteError: If the write operation fails.
        """
        ...

    async def list_policies(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[OrgFact, ...]:
        """List core policy facts, optionally paginated.

        ``limit=None`` (the default) preserves the historical
        "return everything" contract for callers that pre-date
        pagination. When ``limit`` is set, the implementation MUST
        honour ``offset`` and slice the policy snapshot consistently
        with its intrinsic ordering (static config first, then
        dynamically written facts, in the reference impl).

        Returns:
            Tuple of core policy facts (full or sliced view).

        Raises:
            OrgMemoryConnectionError: If not connected.
        """
        ...

    async def count_policies(self) -> int:
        """Return the unfiltered count of core policy facts.

        Companion to :meth:`list_policies` for paginated controllers
        that need a total alongside the page.

        Returns:
            Result of type ``int``.
        """
        ...
