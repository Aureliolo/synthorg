"""Knowledge-substrate persistence protocols.

Two repositories back the knowledge substrate:

* :class:`KnowledgeSourceRepository` keyed by ``source_id`` -- the
  registry of ingested corpus sources (project-scoped or global).
* :class:`ChunkProvenanceRepository` keyed by ``chunk_id`` -- per-chunk
  provenance used to resolve a retrieval hit's :class:`Citation`.

Chunk text lives in the memory backend (the vector store); provenance
rows carry only the locator + hash + source linkage needed for
citation. Both protocols compose the generic categories; the two
bespoke methods on the provenance repo are justified under ADR-0001 D7
(see their docstrings).
"""

from typing import Protocol, Self, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import (
    SourceStatus,
    SourceType,
)
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import ChunkProvenanceRow, KnowledgeSource
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)

KnowledgeSourceKey = NotBlankStr
"""Single-string ``source_id`` PK type alias."""

ChunkProvenanceKey = NotBlankStr
"""Single-string ``chunk_id`` PK type alias."""


class KnowledgeSourceFilter(BaseModel):
    """Filter spec for :meth:`KnowledgeSourceRepository.query`.

    Scope semantics combine ``project_id`` and ``include_global``:

    * ``project_id`` set, ``include_global`` False -- that project only.
    * ``project_id`` set, ``include_global`` True -- that project plus
      global sources.
    * ``project_id`` None, ``include_global`` True -- global only.
    * ``project_id`` None, ``include_global`` False -- no scope filter
      (every source, for admin listing).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr | None = Field(
        default=None,
        description="Project scope; None applies no project filter",
    )
    include_global: bool = Field(
        default=False,
        description="Union global (project-less) sources into the result",
    )
    source_type: SourceType | None = Field(
        default=None,
        description="Optional source-type filter",
    )
    status: SourceStatus | None = Field(
        default=None,
        description="Optional status filter",
    )
    stale_only: bool = Field(
        default=False,
        description="Only sources whose status is STALE",
    )

    @model_validator(mode="after")
    def _validate_status_combination(self) -> Self:
        """Reject ``stale_only=True`` combined with a non-STALE status filter.

        ``stale_only`` is a shortcut for the most common stale-listing
        path; pairing it with an explicit ``status`` other than ``STALE``
        produces a query no row can satisfy, which is almost always a
        caller bug rather than the intended empty result.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails validation.
        """
        if (
            self.stale_only
            and self.status is not None
            and self.status is not SourceStatus.STALE
        ):
            msg = (
                f"stale_only=True is incompatible with status={self.status.value!r};"
                " drop one of the two filters"
            )
            raise ValueError(msg)
        return self


class ChunkProvenanceFilter(BaseModel):
    """Filter spec for :meth:`ChunkProvenanceRepository.query`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Owning source identifier")


@runtime_checkable
class KnowledgeSourceRepository(
    IdKeyedRepository[KnowledgeSource, KnowledgeSourceKey],
    FilteredQueryRepository[KnowledgeSource, KnowledgeSourceFilter],
    Protocol,
):
    """CRUD + filtered-query interface for :class:`KnowledgeSource` rows.

    ``save`` is an upsert keyed by ``source_id`` so re-ingest updates the
    row in place (status, chunk_count, content_hash, last_indexed_at).

    Ordering invariant: :meth:`list_items` and :meth:`query` return rows
    in descending ``updated_at`` order (most-recently-touched first),
    with ``source_id`` as a stable tie-breaker.
    """

    @override
    async def save(self, entity: KnowledgeSource) -> None:
        """Persist a source row via upsert (PK ``source_id``)."""
        ...

    @override
    async def get(self, entity_id: KnowledgeSourceKey) -> KnowledgeSource | None:
        """Retrieve a source by ``source_id``, or ``None`` when absent."""
        ...

    @override
    async def delete(self, entity_id: KnowledgeSourceKey) -> bool:
        """Delete a source by ``source_id``. ``True`` iff a row existed."""
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        """List sources across all scopes, most-recent first."""
        ...

    @override
    async def query(
        self,
        filter_spec: KnowledgeSourceFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        """Return sources matching the filter, most-recent first."""
        ...

    @override
    async def count(self, filter_spec: KnowledgeSourceFilter) -> int:
        """Count sources matching the filter spec."""
        ...


@runtime_checkable
class ChunkProvenanceRepository(
    IdKeyedRepository[ChunkProvenanceRow, ChunkProvenanceKey],
    FilteredQueryRepository[ChunkProvenanceRow, ChunkProvenanceFilter],
    Protocol,
):
    """CRUD + filtered-query interface for :class:`ChunkProvenanceRow`.

    Composes the generic categories plus two bespoke methods justified
    under ADR-0001 D7:

    * :meth:`get_many` -- a real performance optimisation: citation
      resolution fetches every chunk on a hit page in one round trip
      rather than issuing one :meth:`get` per hit (N+1).
    * :meth:`delete_by_source` -- a domain invariant: re-indexing a
      source must purge that source's provenance atomically so stale
      citations cannot survive; callers must not approximate it with
      per-row deletes.

    Ordering invariant: :meth:`query` and :meth:`list_items` return rows
    in ascending ``(source_id, chunk_index)`` order.
    """

    @override
    async def save(self, entity: ChunkProvenanceRow) -> None:
        """Persist a provenance row via upsert (PK ``chunk_id``)."""
        ...

    @override
    async def get(self, entity_id: ChunkProvenanceKey) -> ChunkProvenanceRow | None:
        """Retrieve a provenance row by ``chunk_id``, or ``None``."""
        ...

    @override
    async def delete(self, entity_id: ChunkProvenanceKey) -> bool:
        """Delete a provenance row by ``chunk_id``. ``True`` iff existed."""
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ChunkProvenanceRow, ...]:
        """List provenance rows ordered by ``(source_id, chunk_index)``."""
        ...

    @override
    async def query(
        self,
        filter_spec: ChunkProvenanceFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ChunkProvenanceRow, ...]:
        """Return provenance rows for a source, ``chunk_index`` ascending."""
        ...

    @override
    async def count(self, filter_spec: ChunkProvenanceFilter) -> int:
        """Count provenance rows for a source."""
        ...

    async def get_many(
        self,
        chunk_ids: tuple[ChunkProvenanceKey, ...],
    ) -> tuple[ChunkProvenanceRow, ...]:
        """Fetch many provenance rows by id in one round trip (ADR-0001 D7).

        Returns only the rows that exist; missing ids are silently
        omitted. Order is unspecified; callers index by ``chunk_id``.
        """
        ...

    async def delete_by_source(self, source_id: NotBlankStr) -> int:
        """Delete every provenance row for a source (ADR-0001 D7).

        Returns the number of rows removed. Used by re-index to purge
        stale provenance before fresh rows are written.
        """
        ...
