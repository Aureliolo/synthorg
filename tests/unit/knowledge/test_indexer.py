"""Unit tests for :class:`synthorg.knowledge.indexer.KnowledgeIndexer`.

Exercises the freshness path against a real ``InMemoryBackend`` plus a
dict-backed fake provenance repository: all-new embed, changed-only
re-embed, removed-chunk purge, no-op re-index, and source purge.
"""

from datetime import UTC, datetime

import pytest
from tests._shared import FakeClock
from tests.unit.knowledge._fakes import FakeChunkProvenanceRepository

from synthorg.core.enums import (
    ContentKind,
    MemoryCategory,
    SourceStatus,
    SourceType,
)
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_GLOBAL_SCOPE_TAG,
    KNOWLEDGE_MEMORY_NAMESPACE,
    SYSTEM_KNOWLEDGE_AGENT_ID,
)
from synthorg.knowledge.freshness import make_chunk_id
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.models import KnowledgeChunk, KnowledgeSource, WebLocator
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryQuery
from synthorg.persistence.knowledge_protocol import ChunkProvenanceFilter
from synthorg.versioning.hashing import compute_text_hash

pytestmark = pytest.mark.unit

_PROV_FILTER = ChunkProvenanceFilter(source_id=NotBlankStr("src-1"))


def _source(project_id: str | None = "proj-1") -> KnowledgeSource:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    return KnowledgeSource(
        source_id=NotBlankStr("src-1"),
        source_type=SourceType.WEB,
        project_id=NotBlankStr(project_id) if project_id is not None else None,
        uri=NotBlankStr("https://x.test"),
        title="Doc",
        content_hash="a" * 64,
        status=SourceStatus.INDEXED,
        created_at=ts,
        updated_at=ts,
    )


def _chunk(index: int, text: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=make_chunk_id(NotBlankStr("src-1"), index),
        source_id=NotBlankStr("src-1"),
        content_kind=ContentKind.DOCUMENT,
        chunk_index=index,
        text=text,
        content_hash=compute_text_hash(text),
        locator=WebLocator(
            url=NotBlankStr("https://x.test"), char_start=0, char_end=len(text)
        ),
    )


async def _make_indexer() -> tuple[
    KnowledgeIndexer, InMemoryBackend, FakeChunkProvenanceRepository
]:
    backend = InMemoryBackend()
    await backend.connect()
    provenance = FakeChunkProvenanceRepository()
    indexer = KnowledgeIndexer(
        backend=backend,
        provenance=provenance,
        clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
    )
    return indexer, backend, provenance


async def _knowledge_count(backend: InMemoryBackend) -> int:
    return await backend.count(
        SYSTEM_KNOWLEDGE_AGENT_ID, category=MemoryCategory.KNOWLEDGE
    )


class TestKnowledgeIndexer:
    async def test_index_all_new(self) -> None:
        indexer, backend, provenance = await _make_indexer()
        chunks = (_chunk(0, "alpha text"), _chunk(1, "beta text"))
        outcome = await indexer.index(source=_source(), chunks=chunks)
        assert outcome.embedded == 2
        assert outcome.removed == 0
        assert outcome.unchanged == 0
        assert await _knowledge_count(backend) == 2
        assert await provenance.count(_PROV_FILTER) == 2

    async def test_reindex_only_changed_reembeds_one(self) -> None:
        indexer, backend, provenance = await _make_indexer()
        await indexer.index(
            source=_source(), chunks=(_chunk(0, "alpha"), _chunk(1, "beta"))
        )
        outcome = await indexer.index(
            source=_source(), chunks=(_chunk(0, "alpha"), _chunk(1, "beta EDITED"))
        )
        assert outcome.embedded == 1
        assert outcome.unchanged == 1
        assert await _knowledge_count(backend) == 2
        row = await provenance.get(NotBlankStr("src-1#1"))
        assert row is not None
        assert row.content_hash == compute_text_hash("beta EDITED")

    async def test_noop_when_identical(self) -> None:
        indexer, _backend, _prov = await _make_indexer()
        chunks = (_chunk(0, "alpha"), _chunk(1, "beta"))
        await indexer.index(source=_source(), chunks=chunks)
        outcome = await indexer.index(source=_source(), chunks=chunks)
        assert outcome.embedded == 0
        assert outcome.unchanged == 2

    async def test_reindex_removes_dropped_chunk(self) -> None:
        indexer, backend, provenance = await _make_indexer()
        await indexer.index(
            source=_source(),
            chunks=(_chunk(0, "alpha"), _chunk(1, "beta"), _chunk(2, "gamma")),
        )
        outcome = await indexer.index(
            source=_source(), chunks=(_chunk(0, "alpha"), _chunk(1, "beta"))
        )
        assert outcome.removed == 1
        assert await _knowledge_count(backend) == 2
        assert await provenance.get(NotBlankStr("src-1#2")) is None

    async def test_purge_source(self) -> None:
        indexer, backend, provenance = await _make_indexer()
        await indexer.index(
            source=_source(), chunks=(_chunk(0, "alpha"), _chunk(1, "beta"))
        )
        removed = await indexer.purge_source(NotBlankStr("src-1"))
        assert removed == 2
        assert await _knowledge_count(backend) == 0
        assert await provenance.count(_PROV_FILTER) == 0

    async def test_global_source_tags_scope_global(self) -> None:
        indexer, backend, _prov = await _make_indexer()
        await indexer.index(source=_source(project_id=None), chunks=(_chunk(0, "x"),))
        hits = await backend.retrieve(
            SYSTEM_KNOWLEDGE_AGENT_ID,
            MemoryQuery(
                text=None,
                categories=frozenset({MemoryCategory.KNOWLEDGE}),
                namespaces=frozenset({KNOWLEDGE_MEMORY_NAMESPACE}),
                tags=(KNOWLEDGE_GLOBAL_SCOPE_TAG,),
                limit=10,
            ),
        )
        assert len(hits) == 1
        assert KNOWLEDGE_GLOBAL_SCOPE_TAG in hits[0].metadata.tags
