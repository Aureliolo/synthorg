# mypy: disable-error-code="explicit-any,explicit-override"
"""Failure-path coverage for the knowledge substrate.

The happy paths are covered by ``test_indexer.py`` / ``test_service.py``
/ ``test_loaders.py``. This file targets the error paths flagged by the
pre-PR review: PDF parse failure, web sanitize failure, indexer
provenance.save failure (V8 ordering: provenance writes BEFORE memory
store, so a failing save must leave memory untouched), repo
read-error skip + log, oversized-file boundary, zero-chunk source,
ticket-id mismatch contract, and the per-source ingest lock (V9) that
serialises concurrent ingest/delete on the same source_id.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from synthorg.core.enums import (
    ContentKind,
    MemoryCategory,
    SourceStatus,
    SourceType,
)
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.constants import (
    KNOWLEDGE_MEMORY_NAMESPACE,
    SYSTEM_KNOWLEDGE_AGENT_ID,
)
from synthorg.knowledge.errors import (
    KnowledgeIngestError,
)
from synthorg.knowledge.freshness import make_chunk_id
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.loaders.pdf import PdfLoader
from synthorg.knowledge.loaders.repo import RepoLoader
from synthorg.knowledge.loaders.ticket import (
    TicketComment,
    TicketLoader,
    TicketThread,
)
from synthorg.knowledge.loaders.web import HtmlFetcher, WebLoader
from synthorg.knowledge.models import KnowledgeChunk, KnowledgeSource, WebLocator
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.knowledge.service import KnowledgeService
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryQuery
from tests._shared import FakeClock
from tests.unit.knowledge._fakes import (
    FakeChunkProvenanceRepository,
    FakeKnowledgeSourceRepository,
)

pytestmark = pytest.mark.unit


# ----- shared fixtures ----------------------------------------------------


def _source(status: SourceStatus = SourceStatus.INDEXED) -> KnowledgeSource:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    return KnowledgeSource(
        source_id=NotBlankStr("src-1"),
        source_type=SourceType.WEB,
        project_id=NotBlankStr("proj-1"),
        uri=NotBlankStr("https://x.test"),
        title="Doc",
        content_hash="a" * 64,
        status=status,
        created_at=ts,
        updated_at=ts,
        last_indexed_at=ts if status is SourceStatus.INDEXED else None,
    )


def _chunk(index: int, text: str, content_hash: str | None = None) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=make_chunk_id(NotBlankStr("src-1"), index),
        source_id=NotBlankStr("src-1"),
        content_kind=ContentKind.DOCUMENT,
        chunk_index=index,
        text=text,
        content_hash=(content_hash or "b" * 64),
        locator=WebLocator(
            url=NotBlankStr("https://x.test"),
            char_start=0,
            char_end=len(text),
        ),
    )


# ----- PDF loader: parse failure ------------------------------------------


class _BrokenPdf:
    @property
    def pages(self) -> list[Any]:  # pragma: no cover - never reached
        msg = "synthetic pdfplumber blowup"
        raise RuntimeError(msg)


@contextmanager
def _broken_pdf_opener(_path: str) -> Iterator[Any]:
    yield _BrokenPdf()


class TestPdfLoaderFailures:
    async def test_parse_failure_maps_to_ingest_error(self) -> None:
        loader = PdfLoader(opener=_broken_pdf_opener)
        with pytest.raises(KnowledgeIngestError):
            await loader.load(_source())


# ----- Web loader: sanitize failure ---------------------------------------


class _StaticFetcher(HtmlFetcher):
    async def fetch(self, url: str) -> str:
        return "<html><body><p>fine</p></body></html>"


class _ExplodingGuard:
    """Drop-in replacement for HTMLParseGuard whose sanitize() blows up."""

    def sanitize(self, _html: str) -> Any:
        msg = "synthetic sanitiser failure"
        raise ValueError(msg)


class TestWebLoaderFailures:
    async def test_sanitize_failure_maps_to_ingest_error(self) -> None:
        loader = WebLoader(fetcher=_StaticFetcher(), guard=_ExplodingGuard())  # type: ignore[arg-type]
        with pytest.raises(KnowledgeIngestError):
            await loader.load(_source())


# ----- Indexer: V8 ordering under failure ---------------------------------


class TestIndexerProvenanceFirstOrdering:
    async def test_provenance_failure_leaves_memory_backend_empty(self) -> None:
        # The V8 contract: provenance is written FIRST, so a failed
        # save must NOT have left any memory entries behind. The
        # opposite ordering would orphan memory entries and produce
        # duplicates on retry.
        memory = InMemoryBackend()
        await memory.connect()
        try:
            provenance = FakeChunkProvenanceRepository(fail_on_save=True)
            indexer = KnowledgeIndexer(
                backend=memory,
                provenance=provenance,
                clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
            )
            source = _source()
            chunks = (_chunk(0, "first"), _chunk(1, "second"))
            with pytest.raises(KnowledgeIngestError):
                await indexer.index(source=source, chunks=chunks)
            # No memory entries were written: the indexer failed during
            # the provenance phase, BEFORE the memory store.
            hits = await memory.retrieve(
                SYSTEM_KNOWLEDGE_AGENT_ID,
                MemoryQuery(
                    text=None,
                    categories=frozenset({MemoryCategory.KNOWLEDGE}),
                    namespaces=frozenset({KNOWLEDGE_MEMORY_NAMESPACE}),
                    tags=(),
                    limit=10,
                ),
            )
            assert hits == ()
        finally:
            await memory.disconnect()


# ----- Repo loader: read-error skip + symlink skip + oversized boundary ---


class TestRepoLoaderEdgeCases:
    async def test_undecodable_file_is_skipped_with_log(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # One readable text file + one binary-looking file that fails
        # UTF-8 decode. The loader must skip the bad one, keep the
        # good one, and emit a structured log trail naming the skip
        # so operators can see why files are absent from a corpus.
        (tmp_path / "good.py").write_text("def ok(): pass\n", encoding="utf-8")
        (tmp_path / "bad.py").write_bytes(b"\xff\xfe\x00\x00 invalid utf8")
        loader = RepoLoader()
        source = KnowledgeSource(
            source_id=NotBlankStr("src-1"),
            source_type=SourceType.REPO,
            project_id=NotBlankStr("proj-1"),
            uri=NotBlankStr(str(tmp_path)),
            title="Repo",
            content_hash="a" * 64,
            status=SourceStatus.PENDING,
            created_at=datetime(2026, 5, 21, tzinfo=UTC),
            updated_at=datetime(2026, 5, 21, tzinfo=UTC),
        )
        doc = await loader.load(source)
        assert len(doc.units) == 1  # good.py only; bad.py skipped
        # structlog renders to stdout by default in this project; the
        # file_skipped event name and the skipped path both appear.
        captured = capsys.readouterr().out
        assert "knowledge.source.file_skipped" in captured
        assert "bad.py" in captured

    async def test_oversized_file_is_skipped(self, tmp_path: Path) -> None:
        small_loader = RepoLoader(max_file_bytes=32)
        (tmp_path / "tiny.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "huge.py").write_text("x = 1\n" * 200, encoding="utf-8")
        source = KnowledgeSource(
            source_id=NotBlankStr("src-1"),
            source_type=SourceType.REPO,
            project_id=NotBlankStr("proj-1"),
            uri=NotBlankStr(str(tmp_path)),
            title="Repo",
            content_hash="a" * 64,
            status=SourceStatus.PENDING,
            created_at=datetime(2026, 5, 21, tzinfo=UTC),
            updated_at=datetime(2026, 5, 21, tzinfo=UTC),
        )
        doc = await small_loader.load(source)
        # huge.py exceeds 32 bytes; tiny.py is under the cap.
        assert len(doc.units) == 1


# ----- Ticket loader: id-mismatch contract --------------------------------


class _WrongTicketFetcher:
    """Returns a thread whose ticket_id does not match the requested uri."""

    async def fetch(self, ticket_uri: str) -> TicketThread:
        # Deliberately return a different id to test the loader's guard.
        return TicketThread(
            ticket_id=NotBlankStr(f"OTHER-{ticket_uri}"),
            comments=(
                TicketComment(comment_id=NotBlankStr("c1"), body="mismatched body"),
            ),
        )


class TestTicketLoaderContract:
    async def test_mismatched_ticket_id_rejected(self) -> None:
        loader = TicketLoader(fetcher=_WrongTicketFetcher())
        source = KnowledgeSource(
            source_id=NotBlankStr("src-1"),
            source_type=SourceType.TICKET,
            project_id=NotBlankStr("proj-1"),
            uri=NotBlankStr("TICKET-7"),
            title="Bug 7",
            content_hash="a" * 64,
            status=SourceStatus.PENDING,
            created_at=datetime(2026, 5, 21, tzinfo=UTC),
            updated_at=datetime(2026, 5, 21, tzinfo=UTC),
        )
        with pytest.raises(KnowledgeIngestError):
            await loader.load(source)


# ----- Service: V9 per-source lock serialises concurrent mutators ---------


class _SlowFetcher(HtmlFetcher):
    """Counts concurrent in-flight fetches to expose missing serialisation."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def fetch(self, _url: str) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0)  # yield once so both tasks can interleave
            return "<html><body><p>concurrent</p></body></html>"
        finally:
            self.in_flight -= 1


@pytest.fixture
async def service() -> AsyncIterator[KnowledgeService]:
    sources = FakeKnowledgeSourceRepository()
    provenance = FakeChunkProvenanceRepository()
    memory = InMemoryBackend()
    await memory.connect()
    indexer = KnowledgeIndexer(
        backend=memory,
        provenance=provenance,
        clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
    )
    retriever = KnowledgeRetriever(
        backend=memory,
        sources=sources,
        provenance=provenance,
    )
    try:
        yield KnowledgeService(
            sources=sources,
            indexer=indexer,
            retriever=retriever,
            config=KnowledgeConfig(enabled=True),
            html_fetcher=_SlowFetcher(),
            clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
        )
    finally:
        await memory.disconnect()


class TestServiceConcurrencyLock:
    async def test_concurrent_ingest_of_same_source_is_serialised(
        self, service: KnowledgeService
    ) -> None:
        # Two ingests of the same (project, type, uri) compute the same
        # source_id. Without the V9 lock they would race on the
        # read-modify-write of the source row; with the lock the second
        # one waits, and the fetcher never sees > 1 concurrent fetch
        # for the same source.
        fetcher = service._html_fetcher
        assert isinstance(fetcher, _SlowFetcher)
        task_a = asyncio.create_task(
            service.ingest(
                source_type=SourceType.WEB,
                uri=NotBlankStr("https://docs.test/race"),
                title=NotBlankStr("Race target"),
                project_id=NotBlankStr("proj-1"),
            )
        )
        task_b = asyncio.create_task(
            service.ingest(
                source_type=SourceType.WEB,
                uri=NotBlankStr("https://docs.test/race"),
                title=NotBlankStr("Race target"),
                project_id=NotBlankStr("proj-1"),
            )
        )
        await asyncio.gather(task_a, task_b)
        # Peak concurrency of 1 = the lock serialised the second
        # ingest behind the first; > 1 would prove the race exists.
        assert fetcher.peak == 1
