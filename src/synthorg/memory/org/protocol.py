"""OrgMemoryBackend protocol — lifecycle + org memory operations.

Application code depends on this protocol for shared organizational
memory storage and retrieval.  Concrete backends implement this
protocol to provide company-wide knowledge management.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.org.models import (
    OrgFact,  # noqa: TC001
    OrgFactAuthor,  # noqa: TC001
    OrgFactWriteRequest,  # noqa: TC001
    OrgMemoryQuery,  # noqa: TC001
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

    async def list_policies(self) -> tuple[OrgFact, ...]:
        """List all core policy facts.

        Returns:
            Tuple of core policy facts.

        Raises:
            OrgMemoryConnectionError: If not connected.
        """
        ...
