"""ResumeIntent repository protocol.

Concrete implementations live in the backend modules
(``synthorg.persistence.sqlite.resume_intent_repo`` and
``synthorg.persistence.postgres.resume_intent_repo``).
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.resume_intent import ResumeIntent
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class ResumeIntentRepository(
    IdKeyedRepository[ResumeIntent, NotBlankStr],
    Protocol,
):
    """CRUD interface for in-flight approval resume intents.

    Composes :class:`IdKeyedRepository` (ADR-0001) with no bespoke
    methods: the entity is keyed by ``approval_id``, so id lookup is
    approval lookup, and the startup drain enumerates every row through
    :meth:`list_items`.
    """

    @override
    async def save(self, entity: ResumeIntent, /) -> None:
        """Record an in-flight resume intent, keeping any earlier one.

        Insert-if-absent rather than an upsert (ADR-0001 D7 reading of
        ``save``): the earliest recorded marker for an approval is the
        one that brackets its decision, so a later caller racing the same
        approval must not overwrite the timestamp the drain reasons
        about. Re-recording an already-marked approval is a no-op.

        Args:
            entity: The intent to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> ResumeIntent | None:
        """Retrieve an intent by approval ID.

        Args:
            entity_id: The approval item identifier.

        Returns:
            The intent, or ``None`` if none is in flight.

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
    ) -> tuple[ResumeIntent, ...]:
        """List intents in approval-id order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Intents in ascending approval-id order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Clear the intent for an approval.

        Args:
            entity_id: The approval item identifier.

        Returns:
            ``True`` if a row was cleared, ``False`` if none existed.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
