"""Freshness-aware indexer for the knowledge substrate.

Writes :class:`KnowledgeChunk` content to the memory backend under
:attr:`MemoryCategory.KNOWLEDGE` and records per-chunk provenance. Only
chunks whose content changed (or are new) are re-embedded; unchanged
chunks keep their existing memory entry and provenance row, and removed
chunks are purged. This makes re-indexing a large corpus after a small
edit cheap (one chunk re-embedded, not the whole document).
"""

import asyncio
import builtins
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_CHUNK_TAG_PREFIX,
    KNOWLEDGE_GLOBAL_SCOPE_TAG,
    KNOWLEDGE_KIND_TAG_PREFIX,
    KNOWLEDGE_MEMORY_NAMESPACE,
    KNOWLEDGE_PROJECT_TAG_PREFIX,
    KNOWLEDGE_REINDEX_PAGE_SIZE,
    KNOWLEDGE_SOURCE_TAG_PREFIX,
    SYSTEM_KNOWLEDGE_AGENT_ID,
)
from synthorg.knowledge.errors import KnowledgeIngestError
from synthorg.knowledge.freshness import diff_chunks
from synthorg.knowledge.models import (
    ChunkProvenanceRow,
    KnowledgeChunk,
    KnowledgeSource,
)
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_CHUNKS_INDEX_FAILED,
    KNOWLEDGE_CHUNKS_INDEXED,
    KNOWLEDGE_SOURCE_PURGED,
)
from synthorg.persistence.knowledge_protocol import ChunkProvenanceFilter

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.knowledge_protocol import ChunkProvenanceRepository

logger = get_logger(__name__)


class IndexOutcome(BaseModel):
    """Result of an index pass: how many chunks changed."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    embedded: int = Field(ge=0, description="Chunks (re-)embedded this pass")
    removed: int = Field(ge=0, description="Chunks deleted this pass")
    unchanged: int = Field(ge=0, description="Chunks left untouched")

    @property
    def total_chunks(self) -> int:
        """Total live chunk count after the pass."""
        return self.embedded + self.unchanged


class KnowledgeIndexer:
    """Stores knowledge chunks into the memory backend with provenance."""

    __slots__ = ("_backend", "_clock", "_provenance")

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        provenance: ChunkProvenanceRepository,
        clock: Clock | None = None,
    ) -> None:
        self._backend = backend
        self._provenance = provenance
        self._clock = clock if clock is not None else SystemClock()

    async def index(
        self,
        *,
        source: KnowledgeSource,
        chunks: tuple[KnowledgeChunk, ...],
    ) -> IndexOutcome:
        """Re-embed only changed chunks; purge removed ones.

        Args:
            source: The owning source (provides scope tags + id).
            chunks: Freshly produced chunks for the source.

        Returns:
            An :class:`IndexOutcome` with embedded / removed / unchanged
            counts.

        Raises:
            KnowledgeIngestError: If a backend or provenance operation
                fails.
        """
        existing = await self._existing_hashes(source.source_id)
        diff = diff_chunks(existing_hashes=existing, chunks=chunks)
        if diff.is_noop:
            logger.info(
                KNOWLEDGE_CHUNKS_INDEXED,
                source_id=source.source_id,
                embedded=0,
                removed=0,
                unchanged=len(diff.unchanged),
            )
            return IndexOutcome(embedded=0, removed=0, unchanged=len(diff.unchanged))
        try:
            changed_ids = tuple(
                chunk.chunk_id for chunk in diff.to_embed if chunk.chunk_id in existing
            )
            await self._purge_chunks(
                source_id=source.source_id,
                chunk_ids=(*diff.removed_ids, *changed_ids),
            )
            for removed_id in diff.removed_ids:
                await self._provenance.delete(removed_id)
            await self._embed_chunks(source=source, chunks=diff.to_embed)
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to index chunks for source {source.source_id!r}"
            logger.warning(
                KNOWLEDGE_CHUNKS_INDEX_FAILED,
                source_id=source.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeIngestError(msg) from exc
        logger.info(
            KNOWLEDGE_CHUNKS_INDEXED,
            source_id=source.source_id,
            embedded=len(diff.to_embed),
            removed=len(diff.removed_ids),
            unchanged=len(diff.unchanged),
        )
        return IndexOutcome(
            embedded=len(diff.to_embed),
            removed=len(diff.removed_ids),
            unchanged=len(diff.unchanged),
        )

    async def purge_source(self, source_id: NotBlankStr) -> int:
        """Delete every memory entry and provenance row for a source.

        Used when a source is deleted.

        Returns:
            The number of provenance rows removed.
        """
        existing = await self._existing_hashes(source_id)
        await self._purge_chunks(
            source_id=source_id,
            chunk_ids=tuple(NotBlankStr(cid) for cid in existing),
        )
        removed = await self._provenance.delete_by_source(source_id)
        logger.info(
            KNOWLEDGE_SOURCE_PURGED,
            source_id=source_id,
            provenance_rows_removed=removed,
            chunks_purged=len(existing),
        )
        return removed

    async def _existing_hashes(self, source_id: NotBlankStr) -> dict[str, str]:
        """Return ``chunk_id -> content_hash`` for a source's provenance."""
        rows = await self._provenance.query(
            ChunkProvenanceFilter(source_id=source_id),
            limit=KNOWLEDGE_REINDEX_PAGE_SIZE,
            offset=0,
        )
        result: dict[str, str] = {row.chunk_id: row.content_hash for row in rows}
        offset = len(rows)
        while len(rows) == KNOWLEDGE_REINDEX_PAGE_SIZE:
            rows = await self._provenance.query(
                ChunkProvenanceFilter(source_id=source_id),
                limit=KNOWLEDGE_REINDEX_PAGE_SIZE,
                offset=offset,
            )
            result.update({row.chunk_id: row.content_hash for row in rows})
            offset += len(rows)
        return result

    async def _embed_chunks(
        self,
        *,
        source: KnowledgeSource,
        chunks: tuple[KnowledgeChunk, ...],
    ) -> None:
        """Store provenance rows and memory entries for *chunks*.

        Ordering matters under partial failure. Provenance is written
        BEFORE the memory backend on purpose:

        * If the provenance group fails, no memory entries exist yet, so
          there is nothing to orphan; a retry recomputes hashes against
          a now-empty :meth:`_existing_hashes` and reattempts cleanly.
        * If the memory group fails after provenance succeeded, the
          orphaned provenance rows are inert (citation resolution drops
          unresolved hits, and a retry sees them in
          :meth:`_existing_hashes`, classifies the chunks as "unchanged",
          and reattempts only the missing memory writes via the
          subsequent re-index path).

        The opposite order (memory first) would leave memory entries
        without a provenance row on failure; on retry, ``_existing_hashes``
        returns nothing, so the diff classifies every chunk as new and
        re-embeds them, producing duplicate memory entries for the same
        ``chunk_id``.
        """
        now = self._clock.now()
        async with asyncio.TaskGroup() as tg:
            for chunk in chunks:
                tg.create_task(
                    self._provenance.save(
                        ChunkProvenanceRow(
                            chunk_id=chunk.chunk_id,
                            source_id=source.source_id,
                            content_kind=chunk.content_kind,
                            chunk_index=chunk.chunk_index,
                            content_hash=chunk.content_hash,
                            locator=chunk.locator,
                            created_at=now,
                        )
                    )
                )
        try:
            async with asyncio.TaskGroup() as tg:
                for chunk in chunks:
                    tg.create_task(
                        self._backend.store(
                            SYSTEM_KNOWLEDGE_AGENT_ID,
                            _chunk_to_request(source=source, chunk=chunk),
                        )
                    )
        except builtins.BaseExceptionGroup as group:
            if (
                group.subgroup(builtins.MemoryError) is not None
                or group.subgroup(builtins.RecursionError) is not None
            ):
                raise
            # Roll the just-written provenance rows back so a retry sees
            # the chunks as new (not "unchanged" via stable hashes) and
            # re-embeds the missing memory entries. Without this rollback
            # subsequent runs would hit ``diff.is_noop`` and skip the
            # source forever, leaving orphaned provenance with no memory.
            async with asyncio.TaskGroup() as rollback_tg:
                for chunk in chunks:
                    rollback_tg.create_task(self._provenance.delete(chunk.chunk_id))
            raise

    async def _purge_chunks(
        self,
        *,
        source_id: NotBlankStr,
        chunk_ids: tuple[NotBlankStr, ...],
    ) -> None:
        """Delete the memory entries for the given chunk ids in parallel.

        Sequential retrieve+delete becomes ``O(N)`` network round-trips
        on a large source; a parallel fan-out over a :class:`TaskGroup`
        keeps re-index latency proportional to the slowest call, not the
        slowest sum.
        """
        if not chunk_ids:
            return
        source_tag = NotBlankStr(f"{KNOWLEDGE_SOURCE_TAG_PREFIX}{source_id}")
        async with asyncio.TaskGroup() as tg:
            for chunk_id in chunk_ids:
                tg.create_task(
                    self._purge_chunk_entries(source_tag=source_tag, chunk_id=chunk_id)
                )

    async def _purge_chunk_entries(
        self,
        *,
        source_tag: NotBlankStr,
        chunk_id: NotBlankStr,
    ) -> None:
        """Retrieve and delete every memory entry tagged with *chunk_id*."""
        chunk_tag = NotBlankStr(f"{KNOWLEDGE_CHUNK_TAG_PREFIX}{chunk_id}")
        hits = await self._backend.retrieve(
            SYSTEM_KNOWLEDGE_AGENT_ID,
            MemoryQuery(
                text=None,
                categories=frozenset({MemoryCategory.KNOWLEDGE}),
                namespaces=frozenset({KNOWLEDGE_MEMORY_NAMESPACE}),
                tags=(source_tag, chunk_tag),
                limit=KNOWLEDGE_REINDEX_PAGE_SIZE,
            ),
        )
        if not hits:
            return
        async with asyncio.TaskGroup() as tg:
            for hit in hits:
                tg.create_task(self._backend.delete(SYSTEM_KNOWLEDGE_AGENT_ID, hit.id))


def _chunk_tags(
    *, source: KnowledgeSource, chunk: KnowledgeChunk
) -> tuple[NotBlankStr, ...]:
    """Build the indexing tags carried on a chunk's memory entry.

    Returns:
        The tag tuple: source, chunk, kind, and scope (project or global)
        tags followed by the chunk's own tags.
    """
    scope_tag = (
        KNOWLEDGE_GLOBAL_SCOPE_TAG
        if source.project_id is None
        else NotBlankStr(f"{KNOWLEDGE_PROJECT_TAG_PREFIX}{source.project_id}")
    )
    return (
        NotBlankStr(f"{KNOWLEDGE_SOURCE_TAG_PREFIX}{source.source_id}"),
        NotBlankStr(f"{KNOWLEDGE_CHUNK_TAG_PREFIX}{chunk.chunk_id}"),
        NotBlankStr(f"{KNOWLEDGE_KIND_TAG_PREFIX}{chunk.content_kind.value}"),
        scope_tag,
        *chunk.tags,
    )


def _chunk_to_request(
    *, source: KnowledgeSource, chunk: KnowledgeChunk
) -> MemoryStoreRequest:
    """Translate a :class:`KnowledgeChunk` into a memory store request.

    Returns:
        A ``MemoryStoreRequest`` carrying the chunk text under the
        ``KNOWLEDGE`` category with the chunk's indexing tags.
    """
    return MemoryStoreRequest(
        category=MemoryCategory.KNOWLEDGE,
        namespace=KNOWLEDGE_MEMORY_NAMESPACE,
        content=chunk.text,
        metadata=MemoryMetadata(
            source=source.source_id,
            tags=_chunk_tags(source=source, chunk=chunk),
        ),
    )
