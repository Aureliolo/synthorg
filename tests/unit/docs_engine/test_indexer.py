"""Unit tests for :class:`synthorg.docs_engine.indexer.DocIndexer`.

Uses :class:`InMemoryBackend` (a real :class:`MemoryBackend`
implementation, not a mock) so the indexer exercises a faithful
store/retrieve/delete cycle. Avoids ``MagicMock`` per the project
policy that bare mocks at typed boundaries are forbidden.
"""

import pytest

from synthorg.core.enums import DocType, MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_MEMORY_NAMESPACE,
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SLUG_TAG_PREFIX,
    SYSTEM_DOCS_AGENT_ID,
)
from synthorg.docs_engine.indexer import DocIndexer
from synthorg.docs_engine.models import DocChunk
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryQuery

pytestmark = pytest.mark.unit


def _chunk(
    *, slug: str = "q2-status", chunk_index: int = 0, text: str = "x"
) -> DocChunk:
    return DocChunk(
        project_id=NotBlankStr("proj-1"),
        doc_slug=NotBlankStr(slug),
        doc_type=DocType.STATUS_REPORT,
        chunk_index=chunk_index,
        block_ids=(NotBlankStr("block-1"),),
        text=NotBlankStr(text),
        tags=(
            NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}proj-1"),
            NotBlankStr(f"{DOCS_SLUG_TAG_PREFIX}{slug}"),
        ),
    )


async def _make_backend() -> InMemoryBackend:
    backend = InMemoryBackend()
    await backend.connect()
    return backend


async def _project_chunks(backend: InMemoryBackend, slug: str) -> int:
    """Count PROJECT_DOC entries for *slug* via tag filter."""
    entries = await backend.retrieve(
        SYSTEM_DOCS_AGENT_ID,
        MemoryQuery(
            text=None,
            categories=frozenset({MemoryCategory.PROJECT_DOC}),
            namespaces=frozenset({DOCS_MEMORY_NAMESPACE}),
            tags=(NotBlankStr(f"{DOCS_SLUG_TAG_PREFIX}{slug}"),),
            limit=100,
        ),
    )
    return len(entries)


class TestIndexer:
    async def test_index_stores_each_chunk(self) -> None:
        backend = await _make_backend()
        try:
            indexer = DocIndexer(backend=backend)
            chunks = (
                _chunk(chunk_index=0, text="first"),
                _chunk(chunk_index=1, text="second"),
                _chunk(chunk_index=2, text="third"),
            )
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("q2-status"),
                chunks=chunks,
            )
            assert await _project_chunks(backend, "q2-status") == 3
        finally:
            await backend.disconnect()

    async def test_reindex_replaces_prior_chunks(self) -> None:
        backend = await _make_backend()
        try:
            indexer = DocIndexer(backend=backend)
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("q2-status"),
                chunks=(_chunk(text="v1-a"), _chunk(text="v1-b")),
            )
            assert await _project_chunks(backend, "q2-status") == 2
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("q2-status"),
                chunks=(_chunk(text="v2-a"),),
            )
            assert await _project_chunks(backend, "q2-status") == 1
        finally:
            await backend.disconnect()

    async def test_index_with_no_chunks_clears_prior(self) -> None:
        backend = await _make_backend()
        try:
            indexer = DocIndexer(backend=backend)
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("q2-status"),
                chunks=(_chunk(text="x"),),
            )
            assert await _project_chunks(backend, "q2-status") == 1
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("q2-status"),
                chunks=(),
            )
            assert await _project_chunks(backend, "q2-status") == 0
        finally:
            await backend.disconnect()

    async def test_indexer_isolates_other_slugs(self) -> None:
        backend = await _make_backend()
        try:
            indexer = DocIndexer(backend=backend)
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("doc-a"),
                chunks=(_chunk(slug="doc-a", text="a-1"),),
            )
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("doc-b"),
                chunks=(_chunk(slug="doc-b", text="b-1"),),
            )
            # Re-index doc-a; doc-b stays.
            await indexer.index(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("doc-a"),
                chunks=(_chunk(slug="doc-a", text="a-2"),),
            )
            assert await _project_chunks(backend, "doc-a") == 1
            assert await _project_chunks(backend, "doc-b") == 1
        finally:
            await backend.disconnect()
