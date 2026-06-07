"""Hybrid retrieval + citation resolution for the knowledge substrate.

Reuses the memory backend's dense + BM25 + RRF hybrid retrieval (via the
per-agent ``retrieve`` path under :data:`SYSTEM_KNOWLEDGE_AGENT_ID`) and
resolves each hit's :class:`Citation` from the provenance repository.

Scope is enforced at query time: memory tag filters are AND-semantics,
so a project-scoped search runs two parallel retrieves (the project tag
and the global tag) and merges by descending relevance. This prevents a
hit from another project leaking into the result.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_CHUNK_TAG_PREFIX,
    KNOWLEDGE_GLOBAL_SCOPE_TAG,
    KNOWLEDGE_MEMORY_NAMESPACE,
    KNOWLEDGE_PROJECT_TAG_PREFIX,
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
    SYSTEM_KNOWLEDGE_AGENT_ID,
)
from synthorg.knowledge.errors import KnowledgeRetrievalError
from synthorg.knowledge.models import Citation, KnowledgeHit
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_CITATION_UNRESOLVED,
    KNOWLEDGE_SEARCH_FAILED,
    KNOWLEDGE_SEARCHED,
)

if TYPE_CHECKING:
    from synthorg.knowledge.models import ChunkProvenanceRow, KnowledgeSource
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.knowledge_protocol import (
        ChunkProvenanceRepository,
        KnowledgeSourceRepository,
    )

logger = get_logger(__name__)


def _tag_value(tags: tuple[str, ...], prefix: str) -> str | None:
    """Return the suffix of the first tag matching *prefix*, else None."""
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


class KnowledgeRetriever:
    """Searches the knowledge corpus and resolves citations."""

    __slots__ = ("_backend", "_provenance", "_sources")

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        sources: KnowledgeSourceRepository,
        provenance: ChunkProvenanceRepository,
    ) -> None:
        self._backend = backend
        self._sources = sources
        self._provenance = provenance

    async def search(
        self,
        *,
        query: NotBlankStr,
        project_id: NotBlankStr | None = None,
        limit: int = KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    ) -> tuple[KnowledgeHit, ...]:
        """Return cited knowledge hits for *query* within scope.

        Args:
            query: Natural-language search text.
            project_id: Restrict to this project plus global sources.
                ``None`` searches global sources only.
            limit: Maximum hits to return (bounded by the module cap).

        Returns:
            Hits ordered by descending relevance, each with a resolvable
            citation. Hits whose provenance cannot be resolved are
            dropped (logged), so every returned hit is citable.

        Raises:
            KnowledgeRetrievalError: If the backend search fails.
        """
        effective_limit = max(1, min(limit, KNOWLEDGE_SEARCH_MAX_LIMIT))
        try:
            entries = await self._fetch_scoped(
                query=query, project_id=project_id, limit=effective_limit
            )
        except Exception as exc:
            reraise_critical(exc)
            msg = "Knowledge search failed"
            logger.warning(
                KNOWLEDGE_SEARCH_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeRetrievalError(msg) from exc
        resolved = await self._resolve_citations(entries)
        hits = resolved[:effective_limit]
        logger.debug(
            KNOWLEDGE_SEARCHED,
            project_id=project_id,
            hit_count=len(hits),
        )
        return hits

    async def _fetch_scoped(
        self,
        *,
        query: NotBlankStr,
        project_id: NotBlankStr | None,
        limit: int,
    ) -> tuple[MemoryEntry, ...]:
        """Run scope-filtered hybrid retrieval and merge by relevance.

        Returns:
            Global-scope entries when ``project_id`` is ``None``, else the
            project and global entries merged by descending relevance.
        """
        global_query = self._scoped_query(
            query=query, scope_tag=KNOWLEDGE_GLOBAL_SCOPE_TAG, limit=limit
        )
        if project_id is None:
            return await self._backend.retrieve(SYSTEM_KNOWLEDGE_AGENT_ID, global_query)
        project_tag = NotBlankStr(f"{KNOWLEDGE_PROJECT_TAG_PREFIX}{project_id}")
        project_query = self._scoped_query(
            query=query, scope_tag=project_tag, limit=limit
        )
        async with asyncio.TaskGroup() as tg:
            project_task = tg.create_task(
                self._backend.retrieve(SYSTEM_KNOWLEDGE_AGENT_ID, project_query)
            )
            global_task = tg.create_task(
                self._backend.retrieve(SYSTEM_KNOWLEDGE_AGENT_ID, global_query)
            )
        return _merge_by_relevance(project_task.result(), global_task.result())

    @staticmethod
    def _scoped_query(
        *, query: NotBlankStr, scope_tag: NotBlankStr, limit: int
    ) -> MemoryQuery:
        return MemoryQuery(
            text=query,
            categories=frozenset({MemoryCategory.KNOWLEDGE}),
            namespaces=frozenset({KNOWLEDGE_MEMORY_NAMESPACE}),
            tags=(scope_tag,),
            limit=limit,
        )

    async def _resolve_citations(
        self, entries: tuple[MemoryEntry, ...]
    ) -> tuple[KnowledgeHit, ...]:
        """Resolve each entry's chunk id to a full citation.

        Returns:
            One ``KnowledgeHit`` per entry whose provenance and source
            both resolve; entries that cannot be cited are dropped.
        """
        chunk_ids = tuple(
            NotBlankStr(cid)
            for entry in entries
            if (cid := _tag_value(entry.metadata.tags, KNOWLEDGE_CHUNK_TAG_PREFIX))
            is not None
        )
        provenance = await self._provenance.get_many(chunk_ids)
        prov_by_chunk: dict[str, ChunkProvenanceRow] = {
            row.chunk_id: row for row in provenance
        }
        source_ids = {row.source_id for row in provenance}
        sources = await self._load_sources(tuple(source_ids))
        hits: list[KnowledgeHit] = []
        for entry in entries:
            chunk_id = _tag_value(entry.metadata.tags, KNOWLEDGE_CHUNK_TAG_PREFIX)
            row = prov_by_chunk.get(chunk_id) if chunk_id is not None else None
            source = sources.get(row.source_id) if row is not None else None
            if row is None or source is None:
                logger.warning(
                    KNOWLEDGE_CITATION_UNRESOLVED,
                    chunk_id=chunk_id,
                    has_provenance=row is not None,
                )
                continue
            hits.append(
                KnowledgeHit(
                    chunk_text=entry.content,
                    relevance_score=_clamp(entry.relevance_score),
                    citation=Citation(
                        source_id=row.source_id,
                        chunk_id=row.chunk_id,
                        source_type=source.source_type,
                        title=source.title,
                        uri=source.uri,
                        locator=row.locator,
                        content_hash=row.content_hash,
                    ),
                )
            )
        return tuple(hits)

    async def _load_sources(
        self, source_ids: tuple[str, ...]
    ) -> dict[str, KnowledgeSource]:
        """Fetch the distinct sources referenced by a hit page.

        Returns:
            A map from source id to ``KnowledgeSource`` for every id that
            resolved.
        """
        result: dict[str, KnowledgeSource] = {}
        async with asyncio.TaskGroup() as tg:
            tasks = {
                source_id: tg.create_task(self._sources.get(NotBlankStr(source_id)))
                for source_id in source_ids
            }
        for source_id, task in tasks.items():
            source = task.result()
            if source is not None:
                result[source_id] = source
        return result


def _clamp(score: float | None) -> float:
    """Clamp a backend relevance score into ``[0.0, 1.0]``.

    Returns:
        The score clamped to ``[0.0, 1.0]``, or ``0.0`` when ``None``.
    """
    if score is None:
        return 0.0
    return max(0.0, min(1.0, score))


def _merge_by_relevance(
    *result_sets: tuple[MemoryEntry, ...],
) -> tuple[MemoryEntry, ...]:
    """Merge entry sets by descending relevance, deduplicating by id.

    Returns:
        The entries merged into one tuple, sorted by descending relevance
        with later duplicates of the same id dropped.
    """
    seen: set[str] = set()
    merged: list[MemoryEntry] = []
    for entry in sorted(
        (entry for result in result_sets for entry in result),
        key=lambda entry: (
            entry.relevance_score if entry.relevance_score is not None else 0.0
        ),
        reverse=True,
    ):
        if entry.id in seen:
            continue
        seen.add(entry.id)
        merged.append(entry)
    return tuple(merged)
