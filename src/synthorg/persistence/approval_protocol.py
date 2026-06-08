"""Repository protocol for approval item persistence.

Concrete implementations live in backend modules
(``synthorg.persistence.sqlite.approval_repo.SQLiteApprovalRepository``
and ``synthorg.persistence.postgres.approval_repo.PostgresApprovalRepository``).
The :class:`ApprovalStore` (``synthorg.api.approval_store``) holds a
reference typed against this protocol so the storage implementation
can be swapped without changing the store itself.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)

if TYPE_CHECKING:
    from typing_extensions import TypedDict

    class TransitionKwargs(TypedDict, total=False):
        """Typed kwargs for :meth:`ApprovalRepository.transition_if`."""

        expired_at: object


class ApprovalFilterSpec(BaseModel):
    """Filter spec for ``ApprovalRepository.query`` (ADR-0001).

    All fields optional; an empty spec matches every approval.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: ApprovalStatus | None = Field(default=None)
    risk_level: ApprovalRiskLevel | None = Field(default=None)
    action_type: NotBlankStr | None = Field(default=None)


@runtime_checkable
class ApprovalRepository(
    StatefulRepository[ApprovalItem, NotBlankStr, ApprovalStatus],
    FilteredQueryRepository[ApprovalItem, ApprovalFilterSpec],
    Protocol,
):
    """CRUD + state-transition interface for durable approval-item storage.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Bespoke per D7: :meth:`save_many` and :meth:`expire_if_pending`
    are performance optimisations for bulk operations; :meth:`get_many` is a
    batch-fetch optimisation; :meth:`consume_if_approved` enforces the one-shot
    domain invariant for governed external-access grants.

    All methods are async; non-recoverable errors (``MemoryError``,
    ``RecursionError``) propagate to callers.  Constraint violations
    raise :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: ApprovalItem, /) -> None:
        """Upsert an approval item.

        Args:
            entity: The approval item to persist.

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

        Args:
            items: Approval items to persist.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def transition_if(
        self,
        /,
        entity_id: NotBlankStr,
        from_state: ApprovalStatus,
        to_state: ApprovalStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for approval state transitions.

        Transitions ``entity_id`` from ``from_state`` to ``to_state``
        atomically at the database level. Returns ``True`` iff the row
        was in ``from_state`` and is now in ``to_state``.

        ``**updates`` carries status-correlated columns. The only
        standard key is ``expired_at`` (when transitioning to EXPIRED);
        other keys are ignored for now but reserved for future
        domain-invariant fields.

        Args:
            entity_id: The approval id to transition.
            from_state: Expected current status.
            to_state: Target status.
            **updates: Status-correlated fields (e.g. ``expired_at``).

        Returns:
            ``True`` iff the state transition succeeded, ``False`` on
            state mismatch or when no row exists.

        Raises:
            QueryError: On database errors.
        """
        ...

    async def consume_if_approved(
        self,
        approval_id: NotBlankStr,
        *,
        consumed_at: datetime,
    ) -> bool:
        """Atomic CAS: mark an APPROVED grant as consumed (D7 bespoke).

        Sets ``consumed_at`` iff the row is currently ``approved`` and not
        already consumed, enforcing the one-shot domain invariant for
        governed external-access approvals: a single grant authorises
        exactly one egress. Returns ``True`` iff this call won the race;
        ``False`` on replay (already consumed), state mismatch (not
        approved), or missing row.

        Args:
            approval_id: The approval id to consume.
            consumed_at: Aware UTC timestamp to stamp on success.

        Returns:
            ``True`` iff the grant was consumed by this call.

        Raises:
            QueryError: On database errors.
        """
        ...

    async def expire_if_pending(
        self, ids: Sequence[NotBlankStr]
    ) -> tuple[NotBlankStr, ...]:
        """Batch CAS: flip multiple rows from PENDING to EXPIRED (D7 bespoke).

        Convenience bulk-transition method that calls :meth:`transition_if`
        internally for each id. Returns only the ids that actually
        transitioned (were in PENDING state), allowing the caller to
        drive cache refresh and event callbacks only for truly-changed
        rows without polling the repository.

        Empty input is a no-op (returns ``()``).

        Args:
            ids: Approval ids to expire.

        Returns:
            Subset of input ids that were transitioned from PENDING
            to EXPIRED.

        Raises:
            QueryError: On database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> ApprovalItem | None:
        """Retrieve an approval item by ID.

        Args:
            entity_id: The approval id.

        Returns:
            The approval item, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def get_many(self, ids: Sequence[NotBlankStr]) -> tuple[ApprovalItem, ...]:
        """Batch-fetch approval items by id (D7 bespoke).

        Order is unspecified; callers that need a specific order
        must reorder the result. Missing ids are simply absent from
        the result tuple. Empty input is a no-op (returns ``()``
        without issuing any query).

        Args:
            ids: Approval ids to fetch.

        Returns:
            Tuple of found approval items (order unspecified).

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """List all approval items in order (paginated).

        Results are ordered by ``(created_at DESC, id DESC)`` so cursor
        pagination remains stable under concurrent inserts.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Approval items in descending creation order.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: ApprovalFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items matching the filter spec (paginated).

        Results are ordered by ``(created_at DESC, id DESC)`` so cursor
        pagination remains stable under concurrent inserts.

        Args:
            filter_spec: Carries optional status, risk_level, action_type
                filters (all optional).
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching approval items in descending creation order.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def count(self, filter_spec: ApprovalFilterSpec) -> int:
        """Count approval items matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Count of matching items.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete an approval item by ID.

        Args:
            entity_id: The approval id.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        ...
