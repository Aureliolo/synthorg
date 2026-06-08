"""Top-level orchestration for the knowledge substrate.

:class:`KnowledgeService` ties the loaders, chunkers, indexer, retriever,
and repositories together behind a small API: ingest / reindex / search /
list / get / delete. Ingestion derives a stable ``source_id`` from the
scope + type + uri (so re-ingesting the same source upserts), loads and
chunks the source, short-circuits when the content hash is unchanged, and
records lifecycle status on the source row.
"""

import asyncio
import builtins
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import current_execution_identity
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.chunking import chunk_raw_document
from synthorg.knowledge.constants import (
    KNOWLEDGE_LIST_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
)
from synthorg.knowledge.enums import SourceStatus, SourceType
from synthorg.knowledge.errors import (
    KnowledgeError,
    KnowledgeSourceNotFoundError,
)
from synthorg.knowledge.loaders import build_source_loader
from synthorg.knowledge.models import KnowledgeHit, KnowledgeSource
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    KNOWLEDGE_USAGE_RECORD_FAILED,
    KNOWLEDGE_USAGE_RECORDED,
)
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_INGEST_FAILED,
    KNOWLEDGE_LIST_REQUESTED,
    KNOWLEDGE_REINDEX_COMPLETED,
    KNOWLEDGE_REINDEX_STARTED,
    KNOWLEDGE_SOURCE_DELETED,
    KNOWLEDGE_SOURCE_INGESTED,
    KNOWLEDGE_SOURCE_NOT_FOUND,
    KNOWLEDGE_SOURCE_UNCHANGED,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceFilter
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageRecord
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from synthorg.knowledge.config import KnowledgeConfig
    from synthorg.knowledge.indexer import KnowledgeIndexer
    from synthorg.knowledge.loaders.ticket import TicketFetcher
    from synthorg.knowledge.loaders.web import HtmlFetcher
    from synthorg.knowledge.retrieval import KnowledgeRetriever
    from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
    from synthorg.persistence.knowledge_usage_protocol import (
        KnowledgeUsageRecordRepository,
    )

logger = get_logger(__name__)

_PLACEHOLDER_HASH: NotBlankStr = NotBlankStr("0" * 64)


def derive_source_id(
    *,
    project_id: NotBlankStr | None,
    source_type: SourceType,
    uri: NotBlankStr,
) -> NotBlankStr:
    """Deterministic source id from scope + type + uri (stable re-ingest).

    Returns:
        A stable source id hashed from the scope, source type, and URI, so
        the same source re-ingests onto the same row.
    """
    scope = project_id if project_id is not None else "*"
    return NotBlankStr(compute_text_hash(f"{scope}\0{source_type.value}\0{uri}"))


class KnowledgeService:
    """Ingestion + retrieval orchestration for the knowledge corpus."""

    def __init__(  # noqa: PLR0913 -- injected service collaborators
        self,
        *,
        sources: KnowledgeSourceRepository,
        indexer: KnowledgeIndexer,
        retriever: KnowledgeRetriever,
        config: KnowledgeConfig,
        html_fetcher: HtmlFetcher | None = None,
        ticket_fetcher: TicketFetcher | None = None,
        usage_records: KnowledgeUsageRecordRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._sources = sources
        self._indexer = indexer
        self._retriever = retriever
        self._config = config
        self._html_fetcher = html_fetcher
        self._ticket_fetcher = ticket_fetcher
        self._usage_records = usage_records
        self._clock = clock if clock is not None else SystemClock()
        # Per-source serialisation: concurrent ingest/reindex/delete of
        # the same source_id would otherwise race on the read-modify-
        # write sequence (status clobber, delete-during-index orphans,
        # duplicate embeddings). A registry of locks keyed by source_id
        # guarantees one writer per source at a time; reads (search /
        # list / get) remain unsynchronised. Refcounts let the registry
        # evict an entry once no waiter holds it, so the lock dict does
        # not grow unboundedly across a long-running process's history
        # of unique source ids.
        self._source_locks: dict[NotBlankStr, asyncio.Lock] = {}
        self._source_lock_refcounts: dict[NotBlankStr, int] = {}
        self._source_locks_mutex = asyncio.Lock()

    @asynccontextmanager
    async def _source_lock(self, source_id: NotBlankStr) -> AsyncIterator[None]:
        """Serialise writers for *source_id*; evict the lock when idle."""
        async with self._source_locks_mutex:
            lock = self._source_locks.get(source_id)
            if lock is None:
                lock = asyncio.Lock()
                self._source_locks[source_id] = lock
            self._source_lock_refcounts[source_id] = (
                self._source_lock_refcounts.get(source_id, 0) + 1
            )
        try:
            async with lock:
                yield
        finally:
            async with self._source_locks_mutex:
                remaining = self._source_lock_refcounts[source_id] - 1
                if remaining == 0:
                    del self._source_lock_refcounts[source_id]
                    del self._source_locks[source_id]
                else:
                    self._source_lock_refcounts[source_id] = remaining

    async def ingest(
        self,
        *,
        source_type: SourceType,
        uri: NotBlankStr,
        title: NotBlankStr,
        project_id: NotBlankStr | None = None,
    ) -> KnowledgeSource:
        """Ingest (or re-ingest) a source, re-embedding only changed chunks.

        Returns:
            The persisted ``KnowledgeSource`` after ingestion.
        """
        source_id = derive_source_id(
            project_id=project_id, source_type=source_type, uri=uri
        )
        async with self._source_lock(source_id):
            existing = await self._sources.get(source_id)
            return await self._run_ingest(
                source_id=source_id,
                source_type=source_type,
                uri=uri,
                title=title,
                project_id=project_id,
                existing=existing,
                force=False,
            )

    async def reindex(self, source_id: NotBlankStr) -> KnowledgeSource:
        """Force a re-load + re-index of an existing source.

        Returns:
            The persisted ``KnowledgeSource`` after the forced re-index.
        """
        async with self._source_lock(source_id):
            logger.debug(KNOWLEDGE_REINDEX_STARTED, source_id=source_id)
            existing = await self._require_source(source_id)
            return await self._run_ingest(
                source_id=source_id,
                source_type=existing.source_type,
                uri=existing.uri,
                title=NotBlankStr(existing.title),
                project_id=existing.project_id,
                existing=existing,
                force=True,
            )

    async def search(
        self,
        *,
        query: NotBlankStr,
        project_id: NotBlankStr | None = None,
        limit: int = KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    ) -> tuple[KnowledgeHit, ...]:
        """Return cited knowledge hits for *query* within scope.

        When invoked inside a bound execution scope (an agent run), each
        returned hit is recorded as a :class:`KnowledgeUsageRecord` so a
        deliverable receipt can later enumerate the sources the run
        consulted. Recording is best-effort: a capture failure never
        fails the search.
        """
        hits = await self._retriever.search(
            query=query, project_id=project_id, limit=limit
        )
        await self._record_usage(hits, search_project_id=project_id)
        return hits

    async def _record_usage(
        self,
        hits: tuple[KnowledgeHit, ...],
        *,
        search_project_id: NotBlankStr | None,
    ) -> None:
        """Append a usage record per hit for the bound run (best-effort).

        No-ops when no usage repository is wired, when called outside a
        bound execution scope, or when no project scope can be resolved.
        """
        if self._usage_records is None or not hits:
            return
        identity = current_execution_identity()
        if identity is None:
            return
        project_id = identity.project_id or search_project_id
        if project_id is None:
            return
        recorded = 0
        for hit in hits:
            citation = hit.citation
            try:
                await self._usage_records.append(
                    KnowledgeUsageRecord(
                        task_id=identity.task_id,
                        execution_id=identity.execution_id,
                        project_id=project_id,
                        source_id=citation.source_id,
                        chunk_id=citation.chunk_id,
                        content_hash=citation.content_hash,
                        recorded_at=self._clock.now(),
                    )
                )
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    KNOWLEDGE_USAGE_RECORD_FAILED,
                    execution_id=identity.execution_id,
                    source_id=citation.source_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            recorded += 1
        if recorded:
            logger.debug(
                KNOWLEDGE_USAGE_RECORDED,
                execution_id=identity.execution_id,
                task_id=identity.task_id,
                count=recorded,
            )

    async def list_sources(
        self,
        *,
        project_id: NotBlankStr | None = None,
        include_global: bool = False,
        stale_only: bool = False,
        limit: int = KNOWLEDGE_LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        """List registered sources matching the scope / staleness filter.

        Returns:
            The matching sources for the requested scope, staleness, and
            pagination window.
        """
        logger.debug(
            KNOWLEDGE_LIST_REQUESTED,
            project_id=project_id,
            include_global=include_global,
            stale_only=stale_only,
            limit=limit,
            offset=offset,
        )
        return await self._sources.query(
            KnowledgeSourceFilter(
                project_id=project_id,
                include_global=include_global,
                stale_only=stale_only,
            ),
            limit=limit,
            offset=offset,
        )

    async def get_source(self, source_id: NotBlankStr) -> KnowledgeSource:
        """Return a source by id, raising if absent."""
        return await self._require_source(source_id)

    async def delete_source(self, source_id: NotBlankStr) -> bool:
        """Delete a source and purge its memory entries + provenance.

        Held under the per-source lock so a concurrent ingest/reindex
        cannot leave orphaned memory entries (delete waits for the
        in-flight index, or vice versa).

        Returns:
            ``True`` when the source row was deleted.
        """
        async with self._source_lock(source_id):
            await self._require_source(source_id)
            await self._indexer.purge_source(source_id)
            deleted = await self._sources.delete(source_id)
        logger.info(KNOWLEDGE_SOURCE_DELETED, source_id=source_id, deleted=deleted)
        return deleted

    async def _run_ingest(  # noqa: PLR0913 -- cohesive ingest inputs
        self,
        *,
        source_id: NotBlankStr,
        source_type: SourceType,
        uri: NotBlankStr,
        title: NotBlankStr,
        project_id: NotBlankStr | None,
        existing: KnowledgeSource | None,
        force: bool,
    ) -> KnowledgeSource:
        loader = build_source_loader(
            source_type,
            html_fetcher=self._html_fetcher,
            ticket_fetcher=self._ticket_fetcher,
        )
        provisional = self._provisional(
            source_id=source_id,
            source_type=source_type,
            uri=uri,
            title=title,
            project_id=project_id,
            existing=existing,
        )
        raw = await loader.load(provisional)
        if (
            not force
            and existing is not None
            and existing.status is SourceStatus.INDEXED
            and existing.content_hash == raw.content_hash
        ):
            logger.info(KNOWLEDGE_SOURCE_UNCHANGED, source_id=source_id)
            return existing
        now = self._clock.now()
        pending = provisional.model_copy(
            update={
                "content_hash": raw.content_hash,
                "status": SourceStatus.PENDING,
                "updated_at": now,
            }
        )
        await self._sources.save(pending)
        try:
            outcome = await self._indexer.index(
                source=pending, chunks=chunk_raw_document(raw, config=self._config)
            )
        except builtins.MemoryError, RecursionError:
            raise
        except KnowledgeError as exc:
            await self._sources.save(
                pending.model_copy(
                    update={
                        "status": SourceStatus.FAILED,
                        "last_error": NotBlankStr(safe_error_description(exc)),
                        "updated_at": self._clock.now(),
                    }
                )
            )
            logger.warning(
                KNOWLEDGE_INGEST_FAILED,
                source_id=source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except Exception as exc:
            # Unexpected errors that escape the indexer must still
            # demote the row out of PENDING; a stuck PENDING row hides
            # the failure from operators because the polling /list
            # filter treats it as "still in flight".
            await self._sources.save(
                pending.model_copy(
                    update={
                        "status": SourceStatus.FAILED,
                        "last_error": NotBlankStr(safe_error_description(exc)),
                        "updated_at": self._clock.now(),
                    }
                )
            )
            logger.warning(
                KNOWLEDGE_INGEST_FAILED,
                source_id=source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        # ``last_indexed_at`` must reflect when indexing completed, not
        # when ingest started, so observers can tell at a glance that
        # the row is fresh after a long load.
        completed_at = self._clock.now()
        indexed = pending.model_copy(
            update={
                "status": SourceStatus.INDEXED,
                "chunk_count": outcome.total_chunks,
                "last_indexed_at": completed_at,
                "last_error": None,
                "updated_at": completed_at,
            }
        )
        await self._sources.save(indexed)
        event = KNOWLEDGE_REINDEX_COMPLETED if existing else KNOWLEDGE_SOURCE_INGESTED
        logger.info(
            event,
            source_id=source_id,
            embedded=outcome.embedded,
            removed=outcome.removed,
            chunk_count=outcome.total_chunks,
        )
        return indexed

    def _provisional(  # noqa: PLR0913 -- cohesive source fields
        self,
        *,
        source_id: NotBlankStr,
        source_type: SourceType,
        uri: NotBlankStr,
        title: NotBlankStr,
        project_id: NotBlankStr | None,
        existing: KnowledgeSource | None,
    ) -> KnowledgeSource:
        now = self._clock.now()
        return KnowledgeSource(
            source_id=source_id,
            source_type=source_type,
            project_id=project_id,
            uri=uri,
            title=title,
            content_hash=existing.content_hash if existing else _PLACEHOLDER_HASH,
            status=SourceStatus.PENDING,
            chunk_count=existing.chunk_count if existing else 0,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            last_indexed_at=existing.last_indexed_at if existing else None,
        )

    async def _require_source(self, source_id: NotBlankStr) -> KnowledgeSource:
        source = await self._sources.get(source_id)
        if source is None:
            logger.warning(KNOWLEDGE_SOURCE_NOT_FOUND, source_id=source_id)
            msg = f"Knowledge source not found: {source_id!r}"
            raise KnowledgeSourceNotFoundError(msg)
        return source
