"""Unit tests for retrieval sources, replay, and deduplication."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.knowledge.service import KnowledgeService
from synthorg.research.enums import ResearchSourceType
from synthorg.research.models import (
    AcademicSourceLocator,
    CodeSourceLocator,
    ResearchCitation,
    RetrievedItem,
    SubQuery,
    WebSourceLocator,
)
from synthorg.research.retrieval.dedup import EmbeddingDeduplicator, LexicalDeduplicator
from synthorg.research.retrieval.providers import AcademicResult, CodeResult
from synthorg.research.retrieval.replay import (
    ReplayRetrievalSource,
    build_replay_sources,
)
from synthorg.research.retrieval.sources.academic import AcademicRetrievalSource
from synthorg.research.retrieval.sources.code import CodeRetrievalSource
from synthorg.research.retrieval.sources.knowledge import KnowledgeRetrievalSource
from synthorg.research.retrieval.sources.web import WebRetrievalSource
from synthorg.tools.web.web_search import SearchResult
from tests._shared import FakeClock, mock_of
from tests.unit.research._fakes import (
    FakeAcademicSearchProvider,
    FakeCodeSearchProvider,
    FakeWebSearchProvider,
    knowledge_hit,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_HASH = "c" * 64


def _sub_query(index: int, source_type: ResearchSourceType) -> SubQuery:
    return SubQuery(
        index=index,
        source_type=source_type,
        query_text="widgets",
        intent="probe",
    )


def _item(
    ref_id: str,
    *,
    snippet: str,
    uri: str,
    relevance: float,
    content_hash: str = _HASH,
) -> RetrievedItem:
    return RetrievedItem(
        ref_id=ref_id,
        sub_query_index=0,
        source_type=ResearchSourceType.WEB,
        title="t",
        uri=uri,
        snippet=snippet,
        content_hash=content_hash,
        relevance_score=relevance,
        citation=ResearchCitation(
            ref_id=ref_id,
            source_type=ResearchSourceType.WEB,
            external=WebSourceLocator(url=uri, accessed_at=_NOW),
        ),
    )


# ── Knowledge source ─────────────────────────────────────────────────


async def test_knowledge_source_maps_citation() -> None:
    hit = knowledge_hit(chunk_id="ck-1", text="internal finding")
    service = mock_of[KnowledgeService](
        search=AsyncMock(spec=KnowledgeService.search, return_value=(hit,)),
    )
    source = KnowledgeRetrievalSource(service=service)

    items = await source.retrieve(
        _sub_query(0, ResearchSourceType.KNOWLEDGE), project_id=None
    )

    assert source.source_type is ResearchSourceType.KNOWLEDGE
    assert items[0].ref_id == "src-0-0"
    assert items[0].source_type is ResearchSourceType.KNOWLEDGE
    assert items[0].citation.knowledge is hit.citation
    assert items[0].snippet == "internal finding"


# ── Web source ───────────────────────────────────────────────────────


async def test_web_source_maps_locator_and_skips_empty_url() -> None:
    provider = FakeWebSearchProvider(
        [
            SearchResult(title="A", url="https://a.example", snippet="alpha"),
            SearchResult(title="B", url="", snippet="no url"),
        ]
    )
    source = WebRetrievalSource(provider=provider, clock=FakeClock(start=_NOW))

    items = await source.retrieve(_sub_query(1, ResearchSourceType.WEB))

    assert len(items) == 1
    assert items[0].ref_id == "src-1-0"
    locator = items[0].citation.external
    assert isinstance(locator, WebSourceLocator)
    assert locator.url == "https://a.example"
    assert items[0].relevance_score == 1.0


# ── Academic source ──────────────────────────────────────────────────


async def test_academic_source_maps_locator() -> None:
    provider = FakeAcademicSearchProvider(
        [
            AcademicResult(
                title="Paper",
                identifier="arXiv:2401.1",
                abstract="abstract text",
                year=2024,
                authors=("Doe, J.",),
            )
        ]
    )
    source = AcademicRetrievalSource(provider=provider)

    items = await source.retrieve(_sub_query(2, ResearchSourceType.ACADEMIC))

    locator = items[0].citation.external
    assert isinstance(locator, AcademicSourceLocator)
    assert locator.identifier == "arXiv:2401.1"
    assert locator.year == 2024


# ── Code source ──────────────────────────────────────────────────────


async def test_code_source_maps_locator() -> None:
    provider = FakeCodeSearchProvider(
        [
            CodeResult(
                title="impl",
                repo="owner/repo",
                path="src/a.py",
                snippet="def f(): ...",
                line_start=10,
                line_end=20,
            )
        ]
    )
    source = CodeRetrievalSource(provider=provider)

    items = await source.retrieve(_sub_query(3, ResearchSourceType.CODE))

    locator = items[0].citation.external
    assert isinstance(locator, CodeSourceLocator)
    assert locator.path == "src/a.py"
    assert (locator.line_start, locator.line_end) == (10, 20)


# ── Replay source ────────────────────────────────────────────────────


async def test_replay_source_serves_by_index_and_type() -> None:
    item = _item("src-0-0", snippet="x", uri="https://x", relevance=0.5)
    source = ReplayRetrievalSource(source_type=ResearchSourceType.WEB, items=(item,))

    served = await source.retrieve(_sub_query(0, ResearchSourceType.WEB))
    empty = await source.retrieve(_sub_query(9, ResearchSourceType.WEB))

    assert served == (item,)
    assert empty == ()


async def test_build_replay_sources_covers_every_family() -> None:
    item = _item("src-0-0", snippet="x", uri="https://x", relevance=0.5)
    sources = build_replay_sources((item,))
    assert set(sources) == set(ResearchSourceType)


# ── Deduplication ────────────────────────────────────────────────────


async def test_lexical_dedup_collapses_identical_hash_keeps_best() -> None:
    a = _item("src-0-0", snippet="same", uri="https://a", relevance=0.4)
    b = _item("src-0-1", snippet="same", uri="https://b", relevance=0.9)
    result = await LexicalDeduplicator().dedupe((a, b))
    assert len(result) == 1
    assert result[0].ref_id == "src-0-1"


async def test_lexical_dedup_collapses_canonical_url() -> None:
    a = _item("src-0-0", snippet="alpha words", uri="https://x.test/p", relevance=0.9)
    b = _item(
        "src-0-1",
        snippet="totally different text here",
        uri="http://x.test/p/",
        relevance=0.5,
        content_hash="d" * 64,
    )
    result = await LexicalDeduplicator().dedupe((a, b))
    assert {item.ref_id for item in result} == {"src-0-0"}


async def test_lexical_dedup_keeps_distinct_items() -> None:
    a = _item("src-0-0", snippet="alpha beta gamma", uri="https://a", relevance=0.9)
    b = _item(
        "src-0-1",
        snippet="entirely unrelated delta epsilon",
        uri="https://b",
        relevance=0.5,
        content_hash="e" * 64,
    )
    result = await LexicalDeduplicator().dedupe((a, b))
    assert len(result) == 2


async def test_embedding_dedup_collapses_similar_vectors() -> None:
    a = _item(
        "src-0-0", snippet="one", uri="https://a", relevance=0.9, content_hash="1" * 64
    )
    b = _item(
        "src-0-1", snippet="two", uri="https://b", relevance=0.5, content_hash="2" * 64
    )

    class _Embedder:
        async def embed(self, texts):  # type: ignore[no-untyped-def]
            return tuple((1.0, 0.0) for _ in texts)

    result = await EmbeddingDeduplicator(embedder=_Embedder()).dedupe((a, b))
    assert len(result) == 1
    assert result[0].ref_id == "src-0-0"
