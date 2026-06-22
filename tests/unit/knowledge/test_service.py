"""End-to-end round-trip for :class:`KnowledgeService`.

Exercises the substrate acceptance in-process (InMemoryBackend + fake
repos + a temp repo tree + a fake web fetcher, no external deps): ingest
a mixed corpus, answer with citations that resolve to the exact source
chunk, short-circuit an unchanged re-ingest, re-index only changed chunks
after an edit, and purge on delete.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.enums import SourceStatus, SourceType
from synthorg.knowledge.errors import KnowledgeSourceNotFoundError
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.models import CodeLocator, WebLocator
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.knowledge.service import KnowledgeService, derive_source_id
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from tests._shared import FakeClock
from tests.unit.knowledge._fakes import (
    FakeChunkProvenanceRepository,
    FakeKnowledgeSourceRepository,
)

pytestmark = pytest.mark.unit


class _FakeFetcher:
    def __init__(self, html: str) -> None:
        self._html = html

    async def fetch(self, url: str) -> str:
        return self._html


async def _service(
    html: str = "<p>nothing</p>",
    *,
    repo_root: str = "",
) -> KnowledgeService:
    backend = InMemoryBackend()
    await backend.connect()
    sources = FakeKnowledgeSourceRepository()
    provenance = FakeChunkProvenanceRepository()
    clock = FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC))
    return KnowledgeService(
        sources=sources,
        indexer=KnowledgeIndexer(backend=backend, provenance=provenance, clock=clock),
        retriever=KnowledgeRetriever(
            backend=backend, sources=sources, provenance=provenance
        ),
        config=KnowledgeConfig(repo_root=repo_root),
        html_fetcher=_FakeFetcher(html),
        clock=clock,
    )


class TestKnowledgeServiceRoundTrip:
    async def test_ingest_repo_then_cited_search(self, tmp_path: Path) -> None:
        (tmp_path / "auth.py").write_text(
            "def login(user):\n    return checkout_token(user)\n", encoding="utf-8"
        )
        service = await _service(repo_root=str(tmp_path))
        source = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        assert source.status is SourceStatus.INDEXED
        assert source.chunk_count >= 1

        hits = await service.search(
            query=NotBlankStr("checkout_token"), project_id=NotBlankStr("proj-1")
        )
        assert hits
        citation = hits[0].citation
        assert citation.source_id == source.source_id
        assert isinstance(citation.locator, CodeLocator)
        assert citation.locator.path == "auth.py"
        assert citation.locator.line_start >= 1

    async def test_mixed_corpus_repo_and_global_web(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 'checkout repo'\n", encoding="utf-8")
        service = await _service(
            html="<html><body><p>checkout web</p></body></html>",
            repo_root=str(tmp_path),
        )
        await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        await service.ingest(
            source_type=SourceType.WEB,
            uri=NotBlankStr("https://x.test/guide"),
            title=NotBlankStr("Guide"),
            project_id=None,
        )
        hits = await service.search(
            query=NotBlankStr("checkout"), project_id=NotBlankStr("proj-1")
        )
        kinds = {hit.citation.source_type for hit in hits}
        assert SourceType.REPO in kinds
        assert SourceType.WEB in kinds
        assert any(isinstance(h.citation.locator, WebLocator) for h in hits)

    async def test_unchanged_reingest_short_circuits(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("y = 1\n", encoding="utf-8")
        service = await _service(repo_root=str(tmp_path))
        first = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        second = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        assert second.content_hash == first.content_hash
        assert second.last_indexed_at == first.last_indexed_at

    async def test_edit_then_reindex_surfaces_new_content(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("def f():\n    return 'alpha'\n", encoding="utf-8")
        service = await _service(repo_root=str(tmp_path))
        await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        target.write_text("def f():\n    return 'omega_token'\n", encoding="utf-8")
        reindexed = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        assert reindexed.status is SourceStatus.INDEXED
        hits = await service.search(
            query=NotBlankStr("omega_token"), project_id=NotBlankStr("proj-1")
        )
        assert hits
        assert "omega_token" in hits[0].chunk_text

    async def test_delete_source_purges(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("z = 'checkout'\n", encoding="utf-8")
        service = await _service(repo_root=str(tmp_path))
        source = await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(tmp_path)),
            title=NotBlankStr("Repo"),
            project_id=NotBlankStr("proj-1"),
        )
        assert await service.delete_source(source.source_id) is True
        with pytest.raises(KnowledgeSourceNotFoundError):
            await service.get_source(source.source_id)
        hits = await service.search(
            query=NotBlankStr("checkout"), project_id=NotBlankStr("proj-1")
        )
        assert hits == ()

    async def test_derive_source_id_stable(self) -> None:
        a = derive_source_id(
            project_id=NotBlankStr("proj-1"),
            source_type=SourceType.REPO,
            uri=NotBlankStr("/repo"),
        )
        b = derive_source_id(
            project_id=NotBlankStr("proj-1"),
            source_type=SourceType.REPO,
            uri=NotBlankStr("/repo"),
        )
        assert a == b
