"""Living-documentation persistence protocol.

Keyed by ``(project_id, slug)`` composite primary key. The body bytes
live in the project git workspace; this protocol only persists the
:class:`DocMetadata` projection used by the wiki list view and by the
on-boot replay job that re-indexes commits between
``last_indexed_commit_sha`` and ``head_commit_sha``.
"""

from typing import Protocol, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import (
    DocMetadata,
)
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)

DocsRepositoryKey = tuple[NotBlankStr, NotBlankStr]
"""Composite ``(project_id, slug)`` PK type alias."""


class DocsFilterSpec(BaseModel):
    """Filter spec for :meth:`DocsRepository.query`.

    ``project_id`` is required: docs are always scoped to a project, so
    cross-project listing is intentionally absent. ``doc_type`` and
    ``tag`` narrow the result; ``updated_since`` selects docs touched
    after the supplied timestamp (useful for incremental re-index).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    doc_type: DocType | None = Field(default=None, description="Optional type filter")
    tag: NotBlankStr | None = Field(
        default=None,
        description="Optional tag filter (single tag, exact match)",
    )
    updated_since: AwareDatetime | None = Field(
        default=None,
        description="Only docs with updated_at >= this timestamp",
    )


@runtime_checkable
class DocsRepository(
    IdKeyedRepository[DocMetadata, DocsRepositoryKey],
    FilteredQueryRepository[DocMetadata, DocsFilterSpec],
    Protocol,
):
    """CRUD + filtered-query interface for :class:`DocMetadata` rows.

    Composes :class:`IdKeyedRepository` (composite ``(project_id, slug)``
    PK) and :class:`FilteredQueryRepository` (multi-row queries by
    project + type + tag + recency). ``save`` is an upsert: the engine
    re-writes the same row on every doc update so the metadata row
    always reflects the latest commit SHA.

    Ordering invariant: :meth:`list_items` and :meth:`query` return rows
    in descending ``updated_at`` order (most-recently-touched first), so
    the wiki sidebar surfaces the freshest docs without per-call sort.
    """

    @override
    async def save(self, entity: DocMetadata, /) -> None:
        """Persist a doc metadata row via upsert.

        Args:
            entity: The metadata row; ``(project_id, slug)`` is the PK.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def get(self, entity_id: DocsRepositoryKey, /) -> DocMetadata | None:
        """Retrieve metadata by ``(project_id, slug)``.

        Args:
            entity_id: Composite ``(project_id, slug)`` key.

        Returns:
            The metadata row, or ``None`` if no doc exists with that key.

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
    ) -> tuple[DocMetadata, ...]:
        """List all doc metadata across projects (admin / reindex use).

        Order: descending ``updated_at``.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Doc metadata rows ordered by most-recent ``updated_at``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: DocsRepositoryKey, /) -> bool:
        """Delete a metadata row by ``(project_id, slug)``.

        Args:
            entity_id: Composite ``(project_id, slug)`` key.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: DocsFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DocMetadata, ...]:
        """Return docs matching the filter spec, recency-first.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching metadata rows ordered by descending ``updated_at``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: DocsFilterSpec) -> int:
        """Count docs matching the filter spec.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.

        Returns:
            Number of matching metadata rows.

        Raises:
            QueryError: If the database operation fails.
        """
        ...
