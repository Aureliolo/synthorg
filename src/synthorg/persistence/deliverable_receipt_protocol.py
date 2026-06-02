# module-kind: declarative
"""Deliverable-receipt persistence protocol.

Keyed by ``receipt_id`` (surrogate) with a UNIQUE constraint on
``task_id`` so a task carries exactly one current receipt. ``save`` is an
upsert by ``task_id``: a rebuild (after rework + re-completion) replaces
the prior receipt rather than accumulating duplicates. The full receipt
is stored as a JSON payload; the structured columns exist for filtering.
"""

from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)

if TYPE_CHECKING:
    from synthorg.deliverable_receipts.models import DeliverableReceipt

__all__ = [
    "DeliverableReceiptFilterSpec",
    "DeliverableReceiptRepository",
]


class DeliverableReceiptFilterSpec(BaseModel):
    """Filter spec for :meth:`DeliverableReceiptRepository.query`.

    ``project_id`` is required: receipts are always scoped to a project.
    ``task_id`` and ``deliverable_doc_slug`` narrow to a single receipt.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to the receipt for a single task",
    )
    deliverable_doc_slug: NotBlankStr | None = Field(
        default=None,
        description="Filter to the receipt for a single deliverable doc",
    )


@runtime_checkable
class DeliverableReceiptRepository(
    IdKeyedRepository["DeliverableReceipt", NotBlankStr],
    FilteredQueryRepository["DeliverableReceipt", DeliverableReceiptFilterSpec],
    Protocol,
):
    """CRUD + filtered-query interface for :class:`DeliverableReceipt` rows.

    Composes :class:`IdKeyedRepository` (PK ``receipt_id``) and
    :class:`FilteredQueryRepository`. ``save`` upserts by ``task_id``: the
    UNIQUE constraint on ``task_id`` means a rebuild replaces the prior
    receipt for the same task (its ``receipt_id`` is overwritten).

    Ordering invariant: :meth:`list_items` and :meth:`query` return rows
    in descending ``issued_at`` order (most recent first).
    """

    @override
    async def save(self, entity: DeliverableReceipt) -> None:
        """Persist a receipt via upsert keyed on ``task_id``.

        Args:
            entity: The receipt; ``receipt_id`` is the PK and ``task_id``
                is UNIQUE.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> DeliverableReceipt | None:
        """Retrieve a receipt by ``receipt_id``.

        Args:
            entity_id: The surrogate ``receipt_id``.

        Returns:
            The receipt, or ``None`` if no row exists with that id.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeliverableReceipt, ...]:
        """List receipts across projects, most-recent first.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Receipts ordered by descending ``issued_at``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a receipt by ``receipt_id``.

        Args:
            entity_id: The surrogate ``receipt_id``.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: DeliverableReceiptFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeliverableReceipt, ...]:
        """Return receipts matching the filter, most-recent first.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching receipts ordered by descending ``issued_at``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: DeliverableReceiptFilterSpec) -> int:
        """Count receipts matching the filter spec.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.

        Returns:
            Number of matching receipts.

        Raises:
            QueryError: If the database operation fails.
        """
        ...
