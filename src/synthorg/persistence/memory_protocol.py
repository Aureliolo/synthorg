"""Org-memory repository protocol -- append-only MVCC persistence contract.

Lives in persistence/ so the durable-state contract is colocated
with every other repository protocol. Domain types stay in
``synthorg.memory.org.models``.

This protocol is BESPOKE per ADR-0001 D7 (not composable from generic
categories) because:

1. **Composite key + author tracking**: The retract operation requires
   an author kwarg that tracks who performed the retraction, enforcing
   a domain invariant (all mutations are author-attributed for audit).
2. **Metadata-rich snapshots**: ``snapshot_at()`` returns
   ``OperationLogSnapshot`` (audit metadata) not materialized
   ``OrgFact`` objects; the composite return type cannot fit
   ``MVCCRepository[T, ...]`` signature.
3. **Org-memory domain optimizations**: ``list_by_category()`` and
   ``query()`` are performance-critical for org-wide fact retrieval
   without forcing callers to duplicate filtering logic.
"""

from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.models import (
    OperationLogEntry,
    OperationLogSnapshot,
    OrgFact,
    OrgFactAuthor,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE

_DEFAULT_LIST_LIMIT_FACTS: Final[int] = 5


@runtime_checkable
class OrgFactRepository(Protocol):
    """Org-memory persistence protocol with audit-trail tracking.

    Persists organizational facts (company-wide knowledge such as
    policies, procedures, conventions) with full MVCC and
    author-attributed retraction. All mutations (publish and retract)
    are recorded in an append-only operation log; snapshots are
    materialized on read for efficient point-in-time reconstruction.
    """

    async def save(self, fact: OrgFact, /) -> None:
        """Publish an organizational fact.

        Appends a PUBLISH operation to the log and updates the
        snapshot materialization. Fact IDs must be globally unique.

        Args:
            fact: The org fact to publish (with id, content, category,
                tags, author, created_at fields).

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, fact_id: NotBlankStr, /) -> OrgFact | None:
        """Retrieve the current state of a fact by ID.

        Returns ``None`` if the fact does not exist or has been
        retracted.

        Args:
            fact_id: The fact identifier.

        Returns:
            The active org fact, or ``None`` if absent or retracted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        *,
        categories: frozenset[OrgFactCategory] | None = None,
        text: str | None = None,
        limit: int = _DEFAULT_LIST_LIMIT_FACTS,
        offset: int = 0,
    ) -> tuple[OrgFact, ...]:
        """Query active facts by category and/or text content.

        Returns only non-retracted facts. If both ``categories`` and
        ``text`` are provided, results match both filters (AND).

        Args:
            categories: Optional frozenset of ``OrgFactCategory`` values.
                If provided, only facts in these categories are returned.
            text: Optional substring to search in fact content (case
                sensitive; empty string matches all).
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Active facts matching filters, ordered deterministically
            (by fact_id ascending).

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_by_category(
        self,
        category: OrgFactCategory,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OrgFact, ...]:
        """List all active facts in a category.

        Domain-specific optimization for efficient category-scoped
        retrieval (bespoke D7).

        Args:
            category: The ``OrgFactCategory`` to filter by.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Active facts in the category, ordered by fact_id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(
        self,
        fact_id: NotBlankStr,
        /,
        *,
        author: OrgFactAuthor,
    ) -> bool:
        """Retract a fact by ID (non-destructive delete with author).

        Appends a RETRACT operation to the log and updates snapshot
        materialization. The fact's content is preserved in the
        operation log for audit; the fact appears as retracted in
        snapshots (``retracted_at`` is non-None).

        The ``author`` kwarg is mandatory and enforces domain invariant
        that all mutations are author-attributed (bespoke D7).

        Args:
            fact_id: The fact identifier.
            author: Who is retracting the fact (OrgFactAuthor).

        Returns:
            ``True`` if the fact was active and now retracted,
            ``False`` if the fact did not exist or was already
            retracted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def snapshot_at(
        self,
        timestamp: datetime,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OperationLogSnapshot, ...]:
        """Materialize a bounded page of fact state at a timestamp.

        Returns one ``limit``-sized page (not the whole set) of facts
        (active and retracted) as they were at the given timestamp,
        ordered for a stable cursor walk. Callers needing the complete
        point-in-time snapshot drain every page via
        :func:`synthorg.persistence._shared.collect_all`. Used for
        point-in-time audits and historical reconstruction.

        ``timestamp`` MUST be timezone-aware. Implementations route it
        through :func:`format_iso_utc` (SQLite) or bind it directly as
        a ``TIMESTAMPTZ`` parameter (Postgres); a naive datetime
        either raises ``ValueError`` (SQLite) or silently binds in the
        session timezone (Postgres) -- both surface as a programming
        bug, never as a query that returns a wrong-but-plausible
        snapshot.

        Args:
            timestamp: The UTC timestamp for point-in-time snapshot.
                Must be timezone-aware.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of snapshot rows (one per fact) capturing state at
            ``timestamp``, in ``fact_id`` ascending order so a cursor
            walk is repeatable across the same snapshot. Callers
            needing the whole snapshot drain via
            :func:`synthorg.persistence._shared.collect_all`.

        Raises:
            ValueError: If ``timestamp`` is naive.
            PersistenceError: If the operation fails.
        """
        ...

    async def get_operation_log(
        self,
        fact_id: NotBlankStr,
        /,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OperationLogEntry, ...]:
        """Retrieve a bounded page of the audit trail for a fact.

        Returns one ``limit``-sized page (not the whole trail) of
        PUBLISH and RETRACT operations for the fact in chronological
        order (oldest first), indexed by version number. Version is
        unique per fact so the page order is stable; callers needing
        the full trail drain every page via
        :func:`synthorg.persistence._shared.collect_all`.

        Args:
            fact_id: The fact identifier.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of OperationLogEntry rows in ascending version
            order. Empty tuple if the fact does not exist. Callers
            needing the full trail drain via
            :func:`synthorg.persistence._shared.collect_all`.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
