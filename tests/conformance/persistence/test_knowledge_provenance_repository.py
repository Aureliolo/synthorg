"""Conformance tests for ``ChunkProvenanceRepository`` (SQLite + Postgres).

Asserts the shared contract: id get/save/upsert, the four locator
variants round-trip through ``locator_json``, ``get_many`` batched
lookup, ``query``/``count`` by source, ``delete_by_source`` bulk purge,
and FK cascade from ``knowledge_sources``.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ContentKind, SourceStatus, SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import (
    ChunkProvenanceRow,
    CodeLocator,
    KnowledgeSource,
    PdfLocator,
    ProvenanceLocator,
    TicketLocator,
    WebLocator,
)
from synthorg.persistence.knowledge_protocol import ChunkProvenanceFilter
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_HASH = "b" * 64


def _source(source_id: str = "src-1") -> KnowledgeSource:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    return KnowledgeSource(
        source_id=NotBlankStr(source_id),
        source_type=SourceType.PDF,
        project_id=None,
        uri=NotBlankStr("corpus/spec.pdf"),
        title="Spec",
        content_hash=_HASH,
        status=SourceStatus.INDEXED,
        chunk_count=1,
        created_at=ts,
        updated_at=ts,
    )


def _row(
    *,
    chunk_id: str = "chunk-1",
    source_id: str = "src-1",
    chunk_index: int = 0,
    content_kind: ContentKind = ContentKind.PDF_PAGE,
    locator: ProvenanceLocator | None = None,
) -> ChunkProvenanceRow:
    return ChunkProvenanceRow(
        chunk_id=NotBlankStr(chunk_id),
        source_id=NotBlankStr(source_id),
        content_kind=content_kind,
        chunk_index=chunk_index,
        content_hash=_HASH,
        locator=locator
        if locator is not None
        else PdfLocator(page=1, bbox=(0.0, 1.0, 2.0, 3.0), char_start=0, char_end=10),
        created_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


class TestChunkProvenanceRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        await backend.knowledge_provenance.save(_row())
        fetched = await backend.knowledge_provenance.get(NotBlankStr("chunk-1"))
        assert fetched is not None
        assert isinstance(fetched.locator, PdfLocator)
        assert fetched.locator.page == 1
        assert fetched.locator.bbox == (0.0, 1.0, 2.0, 3.0)

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.knowledge_provenance.get(NotBlankStr("ghost")) is None

    @pytest.mark.parametrize(
        "locator",
        [
            PdfLocator(page=2, char_start=5, char_end=9),
            WebLocator(
                url=NotBlankStr("https://x.test"),
                css_path=NotBlankStr("main>p"),
                char_start=0,
                char_end=4,
            ),
            CodeLocator(
                path=NotBlankStr("a.py"),
                line_start=10,
                line_end=20,
                symbol=NotBlankStr("foo"),
                ast_path=NotBlankStr("mod.foo"),
            ),
            TicketLocator(
                ticket_id=NotBlankStr("T-1"),
                comment_id=NotBlankStr("c-9"),
                char_start=0,
                char_end=3,
            ),
        ],
    )
    async def test_locator_variants_round_trip(
        self, backend: PersistenceBackend, locator: ProvenanceLocator
    ) -> None:
        await backend.knowledge_sources.save(_source())
        await backend.knowledge_provenance.save(_row(locator=locator))
        fetched = await backend.knowledge_provenance.get(NotBlankStr("chunk-1"))
        assert fetched is not None
        assert fetched.locator == locator

    async def test_upsert_replaces(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        await backend.knowledge_provenance.save(_row(chunk_index=0))
        await backend.knowledge_provenance.save(_row(chunk_index=7))
        fetched = await backend.knowledge_provenance.get(NotBlankStr("chunk-1"))
        assert fetched is not None
        assert fetched.chunk_index == 7

    async def test_get_many(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        for i in range(3):
            await backend.knowledge_provenance.save(
                _row(chunk_id=f"chunk-{i}", chunk_index=i)
            )
        rows = await backend.knowledge_provenance.get_many(
            (NotBlankStr("chunk-0"), NotBlankStr("chunk-2"), NotBlankStr("missing"))
        )
        assert {r.chunk_id for r in rows} == {"chunk-0", "chunk-2"}

    async def test_get_many_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.knowledge_provenance.get_many(()) == ()

    async def test_query_by_source_ordered(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        for i in (2, 0, 1):
            await backend.knowledge_provenance.save(
                _row(chunk_id=f"chunk-{i}", chunk_index=i)
            )
        rows = await backend.knowledge_provenance.query(
            ChunkProvenanceFilter(source_id=NotBlankStr("src-1"))
        )
        assert [r.chunk_index for r in rows] == [0, 1, 2]

    async def test_count_matches_query(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        for i in range(3):
            await backend.knowledge_provenance.save(
                _row(chunk_id=f"chunk-{i}", chunk_index=i)
            )
        spec = ChunkProvenanceFilter(source_id=NotBlankStr("src-1"))
        assert await backend.knowledge_provenance.count(spec) == 3
        assert len(await backend.knowledge_provenance.query(spec)) == 3

    async def test_delete_by_source(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        await backend.knowledge_sources.save(_source("src-2"))
        for i in range(3):
            await backend.knowledge_provenance.save(
                _row(chunk_id=f"a-{i}", source_id="src-1", chunk_index=i)
            )
        await backend.knowledge_provenance.save(_row(chunk_id="b-0", source_id="src-2"))
        removed = await backend.knowledge_provenance.delete_by_source(
            NotBlankStr("src-1")
        )
        assert removed == 3
        remaining = await backend.knowledge_provenance.query(
            ChunkProvenanceFilter(source_id=NotBlankStr("src-2"))
        )
        assert {r.chunk_id for r in remaining} == {"b-0"}

    async def test_delete_single(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source())
        await backend.knowledge_provenance.save(_row())
        assert await backend.knowledge_provenance.delete(NotBlankStr("chunk-1")) is True
        assert await backend.knowledge_provenance.get(NotBlankStr("chunk-1")) is None

    async def test_source_delete_cascades_provenance(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting a source removes its provenance rows (FK cascade)."""
        await backend.knowledge_sources.save(_source())
        await backend.knowledge_provenance.save(_row())
        await backend.knowledge_sources.delete(NotBlankStr("src-1"))
        assert await backend.knowledge_provenance.get(NotBlankStr("chunk-1")) is None
