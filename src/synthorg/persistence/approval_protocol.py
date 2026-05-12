"""Repository protocol for approval item persistence.

Concrete implementations live in backend modules
(``synthorg.persistence.sqlite.approval_repo.SQLiteApprovalRepository``
and ``synthorg.persistence.postgres.approval_repo.PostgresApprovalRepository``).
The :class:`ApprovalStore` (``synthorg.api.approval_store``) holds a
reference typed against this protocol so the storage implementation
can be swapped without changing the store itself.

Mirrors the pattern of ``persistence/fine_tune_protocol.py`` and
``persistence/escalation_protocol.py``.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.core.enums import (
    ApprovalRiskLevel,  # noqa: TC001
    ApprovalStatus,  # noqa: TC001
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from collections.abc import Sequence


@runtime_checkable
class ApprovalRepository(Protocol):
    """CRUD interface for durable approval-item storage.

    All methods are async; non-recoverable errors (``MemoryError``,
    ``RecursionError``) propagate to callers.  Constraint violations
    raise :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    async def save(self, item: ApprovalItem) -> None:
        """Upsert an approval item.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    async def save_many(self, items: Sequence[ApprovalItem]) -> None:
        """Upsert multiple approval items in a single transaction.

        All-or-nothing: if any row raises a constraint violation the
        whole batch rolls back. Empty input is a no-op (returns
        without opening a transaction).

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    async def expire_if_pending(
        self, ids: Sequence[NotBlankStr]
    ) -> tuple[NotBlankStr, ...]:
        """Compare-and-set: flip rows still ``PENDING`` to ``EXPIRED``.

        Updates only rows whose current persisted status is still
        ``PENDING``; rows that have transitioned to a terminal status
        (APPROVED, REJECTED, CANCELLED) since the caller's snapshot
        are silently skipped. Returns the ids actually updated, so
        the lazy-expire path in :class:`ApprovalStore` can drive
        cache refresh, audit events, and ``on_expire`` callbacks
        only for rows that truly transitioned -- without this
        compare-and-set a blind upsert would clobber a concurrent
        ``save()`` decision back to ``EXPIRED``.

        Empty input is a no-op (returns ``()``).

        Raises:
            QueryError: On database errors.
        """
        ...

    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None:
        """Get an approval item by ID, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def get_many(self, ids: Sequence[NotBlankStr]) -> tuple[ApprovalItem, ...]:
        """Batch-fetch approval items by id.

        Order is unspecified; callers that need a specific order
        must reorder the result. Missing ids are simply absent from
        the result tuple. Empty input is a no-op (returns ``()``
        without issuing any query).

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items with optional filters.

        Results are ordered by ``(created_at DESC, id DESC)`` so cursor
        pagination remains stable under concurrent inserts.

        Args:
            status: Filter by approval status.
            risk_level: Filter by risk level.
            action_type: Filter by action type.
            limit: Maximum rows to return (must be >= 1).
            offset: Number of rows to skip (must be >= 0).

        Raises:
            QueryError: If the database query fails or
                ``limit < 1`` / ``offset < 0``.
        """
        ...

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Delete an approval item.

        Returns:
            ``True`` if a row was deleted, ``False`` if no match.

        Raises:
            QueryError: If the database query fails.
        """
        ...
