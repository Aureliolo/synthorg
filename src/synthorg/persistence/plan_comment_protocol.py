"""Plan-item comment repository protocol."""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)

__all__ = [
    "PlanItemCommentFilterSpec",
    "PlanItemCommentRepository",
]


class PlanItemCommentFilterSpec(BaseModel):
    """Filter spec for ``PlanItemCommentRepository.query``.

    ``plan_id`` is required (comments are always read per plan); ``item_id``
    narrows to a single item's thread when set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    plan_id: NotBlankStr = Field(description="Plan whose comments to read")
    item_id: NotBlankStr | None = Field(
        default=None,
        description="Narrow to a single item's thread when set",
    )


@runtime_checkable
class PlanItemCommentRepository(
    AppendOnlyRepository[PlanItemComment, PlanItemCommentFilterSpec],
    Protocol,
):
    """Append-only persistence + query interface for ``PlanItemComment``.

    Comments are an immutable discussion thread: no update or delete, so the
    record of who said what and when cannot be rewritten. Composes
    :class:`AppendOnlyRepository` (ADR-0001).
    """

    @override
    async def append(self, event: PlanItemComment, /) -> None:
        """Append one comment (immutable once written).

        Args:
            event: The comment to persist.

        Raises:
            DuplicateRecordError: A comment with the same id already exists.
            QueryError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: PlanItemCommentFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PlanItemComment, ...]:
        """Return a plan's comments oldest-first, so a thread reads in order.

        Unlike the append-only default (newest-first), a discussion thread is
        read chronologically, so this returns rows ordered ``(created_at ASC,
        id ASC)``. Narrows to one item when ``filter_spec.item_id`` is set.

        Args:
            filter_spec: The plan (required) and optional item to read.
            limit: Maximum comments to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching comments, oldest first, capped at *limit* rows.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete comments older than ``threshold`` (retention sweep).

        Args:
            threshold: Comments created before this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the operation fails.
        """
        ...
