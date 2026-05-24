"""Unit tests for the knowledge source loaders.

RepoLoader runs against a real temp tree; PdfLoader and WebLoader use
injected fakes (no pdfplumber/network needed); TicketLoader rejects; the
factory dispatches by source type.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import ContentKind, SourceStatus, SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.errors import (
    KnowledgeSourceUnavailableError,
    KnowledgeValidationError,
)
from synthorg.knowledge.loaders.factory import build_source_loader
from synthorg.knowledge.loaders.pdf import PdfLoader
from synthorg.knowledge.loaders.repo import RepoLoader
from synthorg.knowledge.loaders.ticket import (
    TicketComment,
    TicketLoader,
    TicketThread,
)
from synthorg.knowledge.loaders.web import WebLoader
from synthorg.knowledge.models import CodeLocator, KnowledgeSource, PdfLocator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager

pytestmark = pytest.mark.unit


def _source(source_type: SourceType, uri: str) -> KnowledgeSource:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    return KnowledgeSource(
        source_id=NotBlankStr("src-1"),
        source_type=source_type,
        project_id=NotBlankStr("proj-1"),
        uri=NotBlankStr(uri),
        title="Src",
        content_hash="a" * 64,
        status=SourceStatus.PENDING,
        created_at=ts,
        updated_at=ts,
    )


class _FakePage:
    def __init__(self, number: int, text: str) -> None:
        self.page_number = number
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdf:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages


def _fake_opener(
    pages: list[_FakePage],
) -> Callable[[str], AbstractContextManager[_FakePdf]]:
    @contextmanager
    def opener(_path: str) -> Iterator[_FakePdf]:
        yield _FakePdf(pages)

    return opener


class _FakeFetcher:
    def __init__(self, html: str = "", *, error: Exception | None = None) -> None:
        self._html = html
        self._error = error

    async def fetch(self, url: str) -> str:
        if self._error is not None:
            raise self._error
        return self._html


class TestRepoLoader:
    async def test_walks_text_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "notes.md").write_text("# notes\n", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
        ignored = tmp_path / ".git"
        ignored.mkdir()
        (ignored / "config").write_text("x", encoding="utf-8")

        doc = await RepoLoader().load(_source(SourceType.REPO, str(tmp_path)))
        paths = {
            u.locator.path for u in doc.units if isinstance(u.locator, CodeLocator)
        }
        assert paths == {"a.py", "notes.md"}
        assert all(u.content_kind is ContentKind.CODE for u in doc.units)

    async def test_content_hash_stable(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        src = _source(SourceType.REPO, str(tmp_path))
        first = await RepoLoader().load(src)
        second = await RepoLoader().load(src)
        assert first.content_hash == second.content_hash

    async def test_missing_directory_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        with pytest.raises(KnowledgeSourceUnavailableError):
            await RepoLoader().load(_source(SourceType.REPO, str(missing)))


class TestPdfLoader:
    async def test_one_unit_per_page(self) -> None:
        loader = PdfLoader(
            opener=_fake_opener(
                [_FakePage(1, "checkout alpha"), _FakePage(2, "billing beta")]
            )
        )
        doc = await loader.load(_source(SourceType.PDF, "spec.pdf"))
        assert len(doc.units) == 2
        assert isinstance(doc.units[0].locator, PdfLocator)
        assert doc.units[0].locator.page == 1
        assert doc.units[0].content_kind is ContentKind.PDF_PAGE

    async def test_blank_page_kept_as_empty_unit(self) -> None:
        loader = PdfLoader(opener=_fake_opener([_FakePage(1, "")]))
        doc = await loader.load(_source(SourceType.PDF, "spec.pdf"))
        assert len(doc.units) == 1
        assert doc.units[0].text == ""


class TestWebLoader:
    async def test_sanitises_html_to_text(self) -> None:
        html = "<html><body><p>Checkout flow</p><script>evil()</script></body></html>"
        loader = WebLoader(fetcher=_FakeFetcher(html))
        doc = await loader.load(_source(SourceType.WEB, "https://x.test"))
        assert len(doc.units) == 1
        assert "Checkout flow" in doc.units[0].text
        assert "evil" not in doc.units[0].text

    async def test_fetch_failure_raises_unavailable(self) -> None:
        loader = WebLoader(fetcher=_FakeFetcher(error=ConnectionError("boom")))
        with pytest.raises(KnowledgeSourceUnavailableError):
            await loader.load(_source(SourceType.WEB, "https://x.test"))


class _StubTicketFetcher:
    async def fetch(self, ticket_uri: str) -> TicketThread:
        return TicketThread(
            ticket_id=NotBlankStr(ticket_uri),
            comments=(
                TicketComment(comment_id=NotBlankStr("c1"), body="ticket body content"),
            ),
        )


class _FailingTicketFetcher:
    async def fetch(self, ticket_uri: str) -> TicketThread:
        msg = "transport error"
        raise ConnectionError(msg)


class TestTicketLoader:
    async def test_loads_thread_through_injected_fetcher(self) -> None:
        loader = TicketLoader(fetcher=_StubTicketFetcher())
        doc = await loader.load(_source(SourceType.TICKET, "TICKET-1"))
        assert len(doc.units) == 1
        assert doc.units[0].text == "ticket body content"

    async def test_fetch_failure_raises_unavailable(self) -> None:
        loader = TicketLoader(fetcher=_FailingTicketFetcher())
        with pytest.raises(KnowledgeSourceUnavailableError):
            await loader.load(_source(SourceType.TICKET, "TICKET-2"))


class TestBuildSourceLoader:
    def test_dispatch(self) -> None:
        assert isinstance(build_source_loader(SourceType.PDF), PdfLoader)
        assert isinstance(build_source_loader(SourceType.DESIGN_DOC), PdfLoader)
        assert isinstance(build_source_loader(SourceType.REPO), RepoLoader)
        assert isinstance(
            build_source_loader(SourceType.TICKET, ticket_fetcher=_StubTicketFetcher()),
            TicketLoader,
        )

    def test_web_requires_fetcher(self) -> None:
        with pytest.raises(KnowledgeValidationError):
            build_source_loader(SourceType.WEB)

    def test_ticket_requires_fetcher(self) -> None:
        with pytest.raises(KnowledgeValidationError):
            build_source_loader(SourceType.TICKET)

    def test_web_with_fetcher(self) -> None:
        loader = build_source_loader(SourceType.WEB, html_fetcher=_FakeFetcher())
        assert isinstance(loader, WebLoader)
