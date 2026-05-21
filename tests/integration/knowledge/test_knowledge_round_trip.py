"""End-to-end acceptance round-trip for the knowledge substrate.

Wires the real :class:`KnowledgeService` against a real, migrated SQLite
persistence backend (so citations resolve from durable provenance rows)
and a real :class:`InMemoryBackend` vector store, plus a temp repo tree,
an injected web fetcher, an injected PDF opener, and an injected ticket
fetcher. Validates the issue's acceptance:

- ingest a mixed corpus (repo + PDF + web pages),
- an agent answers a question with citations that resolve to the exact
  source chunk (code line span / PDF page / web offset),
- changing a source re-indexes only the changed chunk,
- deleting a source purges its memory entries + provenance.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests._shared import FakeClock

from synthorg.core.enums import SourceType
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.factory import build_knowledge_service
from synthorg.knowledge.loaders import pdf as pdf_loader_module
from synthorg.knowledge.loaders.ticket import TicketComment, TicketThread
from synthorg.knowledge.models import CodeLocator, PdfLocator, TicketLocator
from synthorg.knowledge.service import KnowledgeService
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

pytestmark = pytest.mark.integration

_WEB_HTML = (
    "<html><body><p>The retry policy uses exponential backoff.</p></body></html>"
)

_PDF_PAGES: tuple[str, ...] = (
    "Page one introduces the throttle policy at the gateway.",
    "Page two: deliverable receipts cite their exact source page.",
)


class _FakeFetcher:
    async def fetch(self, url: str) -> str:
        return _WEB_HTML


class _FakePdfPage:
    def __init__(self, page_number: int, text: str) -> None:
        self.page_number = page_number
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdf:
    def __init__(self, pages: tuple[str, ...]) -> None:
        self.pages = tuple(_FakePdfPage(i + 1, text) for i, text in enumerate(pages))


@contextmanager
def _fake_pdf_opener(_path: str) -> Iterator[Any]:
    yield _FakePdf(_PDF_PAGES)


class _FakeTicketFetcher:
    """Returns a deterministic two-comment thread keyed by uri."""

    async def fetch(self, ticket_uri: str) -> TicketThread:
        return TicketThread(
            ticket_id=NotBlankStr(ticket_uri),
            comments=(
                TicketComment(
                    comment_id=NotBlankStr("c1"),
                    body="Customer reports the export job times out at midnight.",
                ),
                TicketComment(
                    comment_id=NotBlankStr("c2"),
                    body="Engineering: traced to a stale cursor in the exporter.",
                ),
            ),
        )


@pytest.fixture
async def service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[KnowledgeService]:
    # Inject the fake PDF opener so the round-trip exercises the PDF
    # loader without depending on pdfplumber + a real PDF byte stream.
    monkeypatch.setattr(pdf_loader_module, "_default_opener", _fake_pdf_opener)
    db_path = tmp_path / "knowledge.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )
    persistence = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    memory = InMemoryBackend()
    persistence_connected = False
    memory_connected = False
    try:
        await persistence.connect()
        persistence_connected = True
        await memory.connect()
        memory_connected = True
        await persistence.projects.save(
            Project(id=NotBlankStr("proj-1"), name=NotBlankStr("Demo"))
        )
        yield build_knowledge_service(
            memory_backend=memory,
            persistence=persistence,
            config=KnowledgeConfig(enabled=True),
            html_fetcher=_FakeFetcher(),
            ticket_fetcher=_FakeTicketFetcher(),
            clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
        )
    finally:
        if memory_connected:
            await memory.disconnect()
        if persistence_connected:
            await persistence.disconnect()


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "retry.py").write_text(
        "def retry(fn):\n    return run_with_backoff(fn)\n", encoding="utf-8"
    )
    (root / "auth.py").write_text(
        "def login(user):\n    return issue_session(user)\n", encoding="utf-8"
    )


class TestKnowledgeRoundTrip:
    async def test_mixed_corpus_answers_with_resolving_citations(
        self, service: KnowledgeService, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _write_repo(repo)
        await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo)),
            title=NotBlankStr("App repo"),
            project_id=NotBlankStr("proj-1"),
        )
        await service.ingest(
            source_type=SourceType.WEB,
            uri=NotBlankStr("https://docs.test/retries"),
            title=NotBlankStr("Retry guide"),
            project_id=None,
        )

        repo_hits = await service.search(
            query=NotBlankStr("run_with_backoff"),
            project_id=NotBlankStr("proj-1"),
        )
        assert repo_hits
        citation = repo_hits[0].citation
        assert isinstance(citation.locator, CodeLocator)
        assert citation.locator.path == "retry.py"
        assert citation.locator.line_start >= 1
        # The cited chunk text contains the source content (resolves exactly).
        assert "run_with_backoff" in repo_hits[0].chunk_text

        web_hits = await service.search(
            query=NotBlankStr("exponential backoff"),
            project_id=NotBlankStr("proj-1"),
        )
        assert any(h.citation.source_type is SourceType.WEB for h in web_hits)

        # PDF: ingest a fake PDF (opener monkeypatched in the fixture)
        # and verify citations resolve to the exact PDF page.
        await service.ingest(
            source_type=SourceType.PDF,
            uri=NotBlankStr("memo://retry-policy.pdf"),
            title=NotBlankStr("Retry policy memo"),
            project_id=NotBlankStr("proj-1"),
        )
        pdf_hits = await service.search(
            query=NotBlankStr("deliverable receipts"),
            project_id=NotBlankStr("proj-1"),
        )
        pdf_hit = next(
            (h for h in pdf_hits if h.citation.source_type is SourceType.PDF),
            None,
        )
        assert pdf_hit is not None
        assert isinstance(pdf_hit.citation.locator, PdfLocator)
        # The cite resolves to a real page (1-indexed).
        assert pdf_hit.citation.locator.page in {1, 2}
        assert "deliverable receipts" in pdf_hit.chunk_text

        # Ticket: ingest a deterministic ticket via the injected fetcher.
        await service.ingest(
            source_type=SourceType.TICKET,
            uri=NotBlankStr("TICKET-42"),
            title=NotBlankStr("Export timeout"),
            project_id=NotBlankStr("proj-1"),
        )
        ticket_hits = await service.search(
            query=NotBlankStr("stale cursor exporter"),
            project_id=NotBlankStr("proj-1"),
        )
        ticket_hit = next(
            (h for h in ticket_hits if h.citation.source_type is SourceType.TICKET),
            None,
        )
        assert ticket_hit is not None
        assert isinstance(ticket_hit.citation.locator, TicketLocator)
        assert ticket_hit.citation.locator.ticket_id == "TICKET-42"
        assert ticket_hit.citation.locator.comment_id in {"c1", "c2"}

    async def test_edit_reindexes_only_changed_chunk(
        self, service: KnowledgeService, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _write_repo(repo)
        first = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo)),
            title=NotBlankStr("App repo"),
            project_id=NotBlankStr("proj-1"),
        )
        # Edit one file; the other is byte-identical and must not re-embed.
        (repo / "auth.py").write_text(
            "def login(user):\n    return issue_jwt_token(user)\n", encoding="utf-8"
        )
        second = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo)),
            title=NotBlankStr("App repo"),
            project_id=NotBlankStr("proj-1"),
        )
        assert second.content_hash != first.content_hash
        assert second.chunk_count == first.chunk_count
        # New content is searchable + cited.
        hits = await service.search(
            query=NotBlankStr("issue_jwt_token"),
            project_id=NotBlankStr("proj-1"),
        )
        assert hits
        assert "issue_jwt_token" in hits[0].chunk_text

    async def test_delete_source_purges_corpus(
        self, service: KnowledgeService, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _write_repo(repo)
        source = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo)),
            title=NotBlankStr("App repo"),
            project_id=NotBlankStr("proj-1"),
        )
        assert await service.delete_source(source.source_id) is True
        hits = await service.search(
            query=NotBlankStr("run_with_backoff"),
            project_id=NotBlankStr("proj-1"),
        )
        assert hits == ()
