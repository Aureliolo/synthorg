"""Top-level orchestration for the knowledge substrate.

:class:`KnowledgeService` ties the loaders, chunkers, indexer, retriever,
and repositories together behind a small API: ingest / reindex / search /
list / get / delete. Ingestion derives a stable ``source_id`` from the
scope + type + uri (so re-ingesting the same source upserts), loads and
chunks the source, short-circuits when the content hash is unchanged, and
records lifecycle status on the source row.
"""

import builtins
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import SourceStatus, SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.chunking import chunk_raw_document
from synthorg.knowledge.constants import (
    KNOWLEDGE_LIST_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
)
from synthorg.knowledge.errors import (
    KnowledgeError,
    KnowledgeSourceNotFoundError,
)
from synthorg.knowledge.loaders import build_source_loader
from synthorg.knowledge.models import KnowledgeHit, KnowledgeSource
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_INGEST_FAILED,
    KNOWLEDGE_REINDEX_COMPLETED,
    KNOWLEDGE_SOURCE_INGESTED,
    KNOWLEDGE_SOURCE_UNCHANGED,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceFilter
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.knowledge.config import KnowledgeConfig
    from synthorg.knowledge.indexer import KnowledgeIndexer
    from synthorg.knowledge.loaders.web import HtmlFetcher
    from synthorg.knowledge.retrieval import KnowledgeRetriever
    from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository

logger = get_logger(__name__)

_PLACEHOLDER_HASH: NotBlankStr = NotBlankStr("0" * 64)


def derive_source_id(
    *,
    project_id: NotBlankStr | None,
    source_type: SourceType,
    uri: NotBlankStr,
) -> NotBlankStr:
    """Deterministic source id from scope + type + uri (stable re-ingest)."""
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
        clock: Clock | None = None,
    ) -> None:
        self._sources = sources
        self._indexer = indexer
        self._retriever = retriever
        self._config = config
        self._html_fetcher = html_fetcher
        self._clock = clock if clock is not None else SystemClock()

    async def ingest(
        self,
        *,
        source_type: SourceType,
        uri: NotBlankStr,
        title: NotBlankStr,
        project_id: NotBlankStr | None = None,
    ) -> KnowledgeSource:
        """Ingest (or re-ingest) a source, re-embedding only changed chunks."""
        source_id = derive_source_id(
            project_id=project_id, source_type=source_type, uri=uri
        )
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
        """Force a re-load + re-index of an existing source."""
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
        """Return cited knowledge hits for *query* within scope."""
        return await self._retriever.search(
            query=query, project_id=project_id, limit=limit
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
        """List registered sources matching the scope / staleness filter."""
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
        """Delete a source and purge its memory entries + provenance."""
        await self._require_source(source_id)
        await self._indexer.purge_source(source_id)
        return await self._sources.delete(source_id)

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
        loader = build_source_loader(source_type, html_fetcher=self._html_fetcher)
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
        indexed = pending.model_copy(
            update={
                "status": SourceStatus.INDEXED,
                "chunk_count": outcome.total_chunks,
                "last_indexed_at": now,
                "last_error": None,
                "updated_at": now,
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
            msg = f"Knowledge source not found: {source_id!r}"
            raise KnowledgeSourceNotFoundError(msg)
        return source
