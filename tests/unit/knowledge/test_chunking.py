"""Unit tests for the structure-aware chunkers.

Validates the tree-sitter code chunker against real grammars (de-risks
the ``tree_sitter_language_pack`` API), the offset chunker's paragraph
packing + char-offset locators, the unknown-extension line-window
fallback, and the document orchestrator's positional chunk ids.
"""

import pytest

from synthorg.core.enums import ContentKind, SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.chunking import build_chunker, chunk_raw_document
from synthorg.knowledge.chunking.code import CodeChunker
from synthorg.knowledge.chunking.document import OffsetChunker
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.models import (
    CodeLocator,
    PdfLocator,
    RawDocument,
    RawUnit,
    WebLocator,
)

pytestmark = pytest.mark.unit

_CONFIG = KnowledgeConfig()


def _code_unit(text: str, path: str = "a.py") -> RawUnit:
    return RawUnit(
        text=text,
        locator=CodeLocator(
            path=NotBlankStr(path), line_start=1, line_end=max(1, text.count("\n") + 1)
        ),
        content_kind=ContentKind.CODE,
    )


def _doc_unit(text: str) -> RawUnit:
    return RawUnit(
        text=text,
        locator=WebLocator(
            url=NotBlankStr("https://x.test"), char_start=0, char_end=len(text)
        ),
        content_kind=ContentKind.DOCUMENT,
    )


class TestCodeChunker:
    def test_python_functions_split_by_definition(self) -> None:
        chunker = CodeChunker()
        unit = _code_unit("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
        pieces = chunker.chunk_unit(unit)
        symbols = {
            p.locator.symbol for p in pieces if isinstance(p.locator, CodeLocator)
        }
        assert "foo" in symbols
        assert "bar" in symbols

    def test_python_class_is_a_chunk(self) -> None:
        chunker = CodeChunker()
        unit = _code_unit("class Widget:\n    def m(self):\n        return 3\n")
        pieces = chunker.chunk_unit(unit)
        assert any(
            isinstance(p.locator, CodeLocator) and p.locator.symbol == "Widget"
            for p in pieces
        )

    def test_line_spans_are_one_indexed(self) -> None:
        chunker = CodeChunker()
        unit = _code_unit("def foo():\n    return 1\n")
        pieces = chunker.chunk_unit(unit)
        assert pieces
        assert isinstance(pieces[0].locator, CodeLocator)
        assert pieces[0].locator.line_start == 1

    def test_unknown_extension_falls_back_to_line_window(self) -> None:
        chunker = CodeChunker()
        unit = _code_unit("some plain notes\nwith two lines\n", path="notes.xyz")
        pieces = chunker.chunk_unit(unit)
        assert len(pieces) == 1
        assert "plain notes" in pieces[0].text

    def test_blank_input_yields_no_pieces(self) -> None:
        chunker = CodeChunker()
        assert chunker.chunk_unit(_code_unit("   \n  \n")) == ()


class TestOffsetChunker:
    def test_single_paragraph_one_piece(self) -> None:
        chunker = OffsetChunker()
        pieces = chunker.chunk_unit(_doc_unit("A short paragraph of prose."))
        assert len(pieces) == 1
        assert pieces[0].locator.locator_kind == "web"
        assert pieces[0].locator.char_start == 0

    def test_offsets_index_into_source(self) -> None:
        chunker = OffsetChunker()
        text = "First para.\n\nSecond para."
        pieces = chunker.chunk_unit(_doc_unit(text))
        for piece in pieces:
            assert isinstance(piece.locator, WebLocator)
            assert text[piece.locator.char_start : piece.locator.char_end] == piece.text

    def test_rejects_locator_without_offsets(self) -> None:
        chunker = OffsetChunker()
        bad = RawUnit(
            text="x",
            locator=CodeLocator(path=NotBlankStr("a.py"), line_start=1, line_end=1),
            content_kind=ContentKind.DOCUMENT,
        )
        with pytest.raises(TypeError):
            chunker.chunk_unit(bad)


class TestChunkRawDocument:
    def test_positional_ids_across_units(self) -> None:
        raw = RawDocument(
            source_id=NotBlankStr("src-1"),
            source_type=SourceType.PDF,
            uri=NotBlankStr("corpus/x.pdf"),
            title="X",
            content_hash="a" * 64,
            units=(
                RawUnit(
                    text="page one prose",
                    locator=PdfLocator(page=1, char_start=0, char_end=14),
                    content_kind=ContentKind.PDF_PAGE,
                ),
                RawUnit(
                    text="page two prose",
                    locator=PdfLocator(page=2, char_start=0, char_end=14),
                    content_kind=ContentKind.PDF_PAGE,
                ),
            ),
        )
        chunks = chunk_raw_document(raw, config=_CONFIG)
        assert [c.chunk_id for c in chunks] == ["src-1#0", "src-1#1"]
        assert chunks[0].content_hash != chunks[1].content_hash
        assert all(c.source_id == "src-1" for c in chunks)

    def test_build_chunker_dispatch(self) -> None:
        assert isinstance(build_chunker(ContentKind.CODE, _CONFIG), CodeChunker)
        assert isinstance(build_chunker(ContentKind.DOCUMENT, _CONFIG), OffsetChunker)
        assert isinstance(build_chunker(ContentKind.PDF_PAGE, _CONFIG), OffsetChunker)
