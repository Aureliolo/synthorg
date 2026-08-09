"""DeletedEntity repository protocol."""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.deleted_entity import DeletedEntity, DeletedEntityKind
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class DeletedEntityFilterSpec(BaseModel):
    """Filter spec for ``DeletedEntityRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    entity_kind: DeletedEntityKind | None = Field(
        default=None,
        description="Filter by entity kind",
    )
    entity_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by the identifier that was removed",
    )


@runtime_checkable
class DeletedEntityRepository(
    AppendOnlyRepository["DeletedEntity", DeletedEntityFilterSpec],
    Protocol,
):
    """Append-only record of what a deleted task, plan or project was.

    Composes :class:`AppendOnlyRepository` (ADR-0001) with no bespoke
    surface: a tombstone is written once and read back by the identifier a
    surviving record still names, which the generic append/query already
    express.
    """

    @override
    async def append(self, event: DeletedEntity, /) -> None:
        """Persist one tombstone (append-only).

        Args:
            event: The tombstone to record.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: DeletedEntityFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeletedEntity, ...]:
        """Query tombstones with optional filters and pagination.

        Args:
            filter_spec: Carries optional entity kind and id filters.
            limit: Maximum rows to return.
            offset: Rows to skip before applying limit.

        Returns:
            Matching tombstones as a tuple, ordered newest-first.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete tombstones older than threshold (retention).

        Args:
            threshold: Rows older than this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


__all__ = ["DeletedEntityFilterSpec", "DeletedEntityRepository"]
