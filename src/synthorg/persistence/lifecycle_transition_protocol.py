"""LifecycleTransition repository protocol."""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class LifecycleTransitionFilterSpec(BaseModel):
    """Filter spec for ``LifecycleTransitionRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    entity_kind: LifecycleEntityKind | None = Field(
        default=None,
        description="Filter by entity kind",
    )
    entity_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by entity identifier",
    )


@runtime_checkable
class LifecycleTransitionRepository(
    AppendOnlyRepository["LifecycleTransition", LifecycleTransitionFilterSpec],
    Protocol,
):
    """Append-only ledger of plan and project status changes.

    Composes :class:`AppendOnlyRepository` (ADR-0001) with no bespoke
    surface: the ledger is written one row at a time and read back per
    entity, which the generic append/query already express.
    """

    @override
    async def append(self, event: LifecycleTransition, /) -> None:
        """Persist one transition (append-only).

        Args:
            event: The transition to record.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: LifecycleTransitionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[LifecycleTransition, ...]:
        """Query transitions with optional filters and pagination.

        Args:
            filter_spec: Carries optional entity kind and id filters.
            limit: Maximum rows to return.
            offset: Rows to skip before applying limit.

        Returns:
            Matching transitions as a tuple, ordered newest-first.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete transitions older than threshold (retention).

        Args:
            threshold: Rows older than this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


__all__ = ["LifecycleTransitionFilterSpec", "LifecycleTransitionRepository"]
