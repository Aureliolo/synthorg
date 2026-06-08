"""ParkedContext repository protocol."""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository
from synthorg.security.timeout.parked_context import ParkedContext


@runtime_checkable
class ParkedContextRepository(
    IdKeyedRepository[ParkedContext, NotBlankStr],
    Protocol,
):
    """CRUD interface for parked agent execution contexts.

    Composes :class:`IdKeyedRepository` (ADR-0001). Bespoke per D7:
    :meth:`get_by_approval` is a unique-key lookup (each approval has
    at most one parked context) and :meth:`get_by_agent` returns rows
    ordered ``parked_at`` DESC, both of which are simpler/cheaper than
    routing through a ``FilteredQueryRepository.query`` call.
    """

    @override
    async def save(self, entity: ParkedContext, /) -> None:
        """Persist a parked context.

        Args:
            entity: The parked context to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> ParkedContext | None:
        """Retrieve a parked context by ID.

        Args:
            entity_id: The parked context identifier.

        Returns:
            The parked context, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        """List parked contexts in id order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Parked contexts in ascending id order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_approval(self, approval_id: NotBlankStr) -> ParkedContext | None:
        """Retrieve a parked context by approval ID.

        Args:
            approval_id: The approval item identifier.

        Returns:
            The parked context, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        """Retrieve a bounded page of parked contexts for an agent.

        Args:
            agent_id: The agent identifier.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of parked contexts for the agent, ordered by
            ``parked_at`` DESC then ``id`` ascending (stable secondary
            key for deterministic paging). Callers that need every
            parked context drain via
            :func:`synthorg.persistence._shared.collect_all`.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a parked context by ID.

        Args:
            entity_id: The parked context identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
