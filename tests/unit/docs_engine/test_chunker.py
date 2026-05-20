"""Unit tests for :class:`synthorg.docs_engine.chunker.DocChunker`."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import DocType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.chunker import DocChunker
from synthorg.docs_engine.constants import (
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SLUG_TAG_PREFIX,
    DOCS_TYPE_TAG_PREFIX,
)
from synthorg.docs_engine.models import (
    BulletListBlock,
    DecisionBlock,
    DocBlock,
    HeadingBlock,
    LivingDocument,
    MetricBlock,
    ProseBlock,
)

pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def _doc(body: tuple[DocBlock, ...]) -> LivingDocument:
    return LivingDocument(
        slug=NotBlankStr("q2-status"),
        title=NotBlankStr("Q2 status"),
        doc_type=DocType.STATUS_REPORT,
        author_agent_id=NotBlankStr("alice"),
        body=body,
        created_at=_ts(),
        updated_at=_ts(),
    )


class TestChunker:
    def test_emits_one_chunk_per_non_prose_block(self) -> None:
        body = (
            HeadingBlock(level=2, text="Summary"),
            DecisionBlock(decision="Hold", rationale="too early"),
            MetricBlock(name="conv", value="0.12"),
        )
        chunker = DocChunker()
        chunks = chunker.chunk(
            project_id=NotBlankStr("proj-1"),
            doc=_doc(body),
        )
        assert len(chunks) == 3
        assert chunks[0].chunk_index == 0
        assert chunks[1].chunk_index == 1
        assert chunks[2].chunk_index == 2

    def test_block_ids_are_tracked(self) -> None:
        heading = HeadingBlock(level=2, text="Summary")
        prose = ProseBlock(text="hello world")
        chunks = DocChunker().chunk(
            project_id=NotBlankStr("proj-1"),
            doc=_doc((heading, prose)),
        )
        assert chunks[0].block_ids == (heading.block_id,)
        assert chunks[1].block_ids == (prose.block_id,)

    def test_chunks_carry_project_slug_type_tags(self) -> None:
        body = (HeadingBlock(level=2, text="x"),)
        chunks = DocChunker().chunk(
            project_id=NotBlankStr("proj-1"),
            doc=_doc(body),
        )
        tags = chunks[0].tags
        assert f"{DOCS_PROJECT_TAG_PREFIX}proj-1" in tags
        assert f"{DOCS_SLUG_TAG_PREFIX}q2-status" in tags
        assert f"{DOCS_TYPE_TAG_PREFIX}status_report" in tags

    def test_adjacent_short_prose_merges_into_one_chunk(self) -> None:
        body = (
            ProseBlock(text="short one"),
            ProseBlock(text="short two"),
            ProseBlock(text="short three"),
        )
        chunks = DocChunker(target_tokens=1000, max_tokens=2000).chunk(
            project_id=NotBlankStr("proj-1"),
            doc=_doc(body),
        )
        assert len(chunks) == 1
        assert len(chunks[0].block_ids) == 3
        assert "short one" in chunks[0].text
        assert "short three" in chunks[0].text

    def test_prose_run_flushes_around_non_prose(self) -> None:
        body = (
            ProseBlock(text="alpha"),
            ProseBlock(text="beta"),
            HeadingBlock(level=2, text="Section"),
            ProseBlock(text="gamma"),
        )
        chunks = DocChunker(target_tokens=1000, max_tokens=2000).chunk(
            project_id=NotBlankStr("proj-1"),
            doc=_doc(body),
        )
        assert len(chunks) == 3
        # First chunk: merged prose
        assert "alpha" in chunks[0].text
        assert "beta" in chunks[0].text
        # Second chunk: heading
        assert chunks[1].text == "Section"
        # Third chunk: trailing prose
        assert chunks[2].text == "gamma"

    def test_chunker_is_deterministic(self) -> None:
        body = (
            HeadingBlock(level=2, text="Summary"),
            ProseBlock(text="x"),
            DecisionBlock(decision="d", rationale="r"),
        )
        doc = _doc(body)
        chunker = DocChunker()
        a = chunker.chunk(project_id=NotBlankStr("proj-1"), doc=doc)
        b = chunker.chunk(project_id=NotBlankStr("proj-1"), doc=doc)
        assert a == b

    def test_max_tokens_rejection(self) -> None:
        with pytest.raises(ValueError, match=r"max_tokens .* must be >= target_tokens"):
            DocChunker(target_tokens=512, max_tokens=256)

    def test_bullet_list_chunk_text(self) -> None:
        body = (BulletListBlock(items=("foo", "bar", "baz")),)
        chunks = DocChunker().chunk(
            project_id=NotBlankStr("proj-1"),
            doc=_doc(body),
        )
        assert chunks[0].text == "- foo\n- bar\n- baz"
