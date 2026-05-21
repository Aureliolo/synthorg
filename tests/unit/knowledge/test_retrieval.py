"""Unit tests for :class:`synthorg.knowledge.retrieval.KnowledgeRetriever`.

Indexes chunks into a real ``InMemoryBackend`` (substring text match),
then asserts citation resolution, project + global scope union, scope
isolation across projects, and that hits without provenance are dropped.
"""

from datetime import UTC, datetime

import pytest
from tests._shared import FakeClock
from tests.unit.knowledge._fakes import (
    FakeChunkProvenanceRepository,
    FakeKnowledgeSourceRepository,
)

from synthorg.core.enums import ContentKind, SourceStatus, SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.freshness import make_chunk_id
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.models import (
    CodeLocator,
    KnowledgeChunk,
    KnowledgeSource,
)
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.versioning.hashing import compute_text_hash

pytestmark = pytest.mark.unit


class _Harness:
    def __init__(self) -> None:
        self.backend = InMemoryBackend()
        self.sources = FakeKnowledgeSourceRepository()
        self.provenance = FakeChunkProvenanceRepository()
        self.indexer = KnowledgeIndexer(
            backend=self.backend,
            provenance=self.provenance,
            clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
        )
        self.retriever = KnowledgeRetriever(
            backend=self.backend,
            sources=self.sources,
            provenance=self.provenance,
        )

    async def add_source(
        self, *, source_id: str, project_id: str | None, text: str
    ) -> KnowledgeSource:
        ts = datetime(2026, 5, 21, tzinfo=UTC)
        source = KnowledgeSource(
            source_id=NotBlankStr(source_id),
            source_type=SourceType.REPO,
            project_id=NotBlankStr(project_id) if project_id is not None else None,
            uri=NotBlankStr(f"repo/{source_id}"),
            title=f"Source {source_id}",
            content_hash="a" * 64,
            status=SourceStatus.INDEXED,
            created_at=ts,
            updated_at=ts,
        )
        await self.sources.save(source)
        chunk = KnowledgeChunk(
            chunk_id=make_chunk_id(NotBlankStr(source_id), 0),
            source_id=NotBlankStr(source_id),
            content_kind=ContentKind.CODE,
            chunk_index=0,
            text=text,
            content_hash=compute_text_hash(text),
            locator=CodeLocator(
                path=NotBlankStr(f"{source_id}.py"), line_start=1, line_end=3
            ),
        )
        await self.indexer.index(source=source, chunks=(chunk,))
        return source


async def _harness() -> _Harness:
    h = _Harness()
    await h.backend.connect()
    return h


class TestKnowledgeRetriever:
    async def test_hit_resolves_citation(self) -> None:
        h = await _harness()
        await h.add_source(
            source_id="src-1", project_id="proj-1", text="checkout flow alpha"
        )
        hits = await h.retriever.search(
            query=NotBlankStr("checkout"), project_id=NotBlankStr("proj-1")
        )
        assert len(hits) == 1
        citation = hits[0].citation
        assert citation.source_id == "src-1"
        assert citation.chunk_id == "src-1#0"
        assert citation.source_type is SourceType.REPO
        assert citation.locator.locator_kind == "code"
        assert citation.content_hash == compute_text_hash("checkout flow alpha")
        assert hits[0].chunk_text == "checkout flow alpha"

    async def test_project_plus_global_scope(self) -> None:
        h = await _harness()
        await h.add_source(source_id="proj", project_id="proj-1", text="checkout alpha")
        await h.add_source(source_id="glob", project_id=None, text="checkout beta")
        await h.add_source(
            source_id="other", project_id="proj-2", text="checkout gamma"
        )
        hits = await h.retriever.search(
            query=NotBlankStr("checkout"), project_id=NotBlankStr("proj-1")
        )
        assert {hit.citation.source_id for hit in hits} == {"proj", "glob"}

    async def test_global_only_search(self) -> None:
        h = await _harness()
        await h.add_source(source_id="proj", project_id="proj-1", text="checkout alpha")
        await h.add_source(source_id="glob", project_id=None, text="checkout beta")
        hits = await h.retriever.search(query=NotBlankStr("checkout"))
        assert {hit.citation.source_id for hit in hits} == {"glob"}

    async def test_unresolved_provenance_dropped(self) -> None:
        h = await _harness()
        await h.add_source(
            source_id="src-1", project_id="proj-1", text="checkout flow alpha"
        )
        # Delete the provenance row but leave the memory entry: the hit
        # can no longer be cited, so the retriever must drop it.
        await h.provenance.delete(NotBlankStr("src-1#0"))
        hits = await h.retriever.search(
            query=NotBlankStr("checkout"), project_id=NotBlankStr("proj-1")
        )
        assert hits == ()
