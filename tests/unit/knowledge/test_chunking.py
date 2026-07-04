"""Unit tests for the structure-aware chunkers.

Validates the tree-sitter code chunker against real grammars (de-risks
the ``tree_sitter_language_pack`` API), the offset chunker's paragraph
packing + char-offset locators, the unknown-extension line-window
fallback, and the document orchestrator's positional chunk ids.
"""

import time

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.chunking import build_chunker, chunk_raw_document
from synthorg.knowledge.chunking.code import CodeChunker
from synthorg.knowledge.chunking.document import OffsetChunker
from synthorg.knowledge.chunking.protocol import ChunkPiece
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.enums import ContentKind, SourceType
from synthorg.knowledge.errors import KnowledgeDependencyError
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
    async def test_positional_ids_across_units(self) -> None:
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
        chunks = await chunk_raw_document(raw, config=_CONFIG)
        assert [c.chunk_id for c in chunks] == ["src-1#0", "src-1#1"]
        assert chunks[0].content_hash != chunks[1].content_hash
        assert all(c.source_id == "src-1" for c in chunks)

    def test_build_chunker_dispatch(self) -> None:
        assert isinstance(build_chunker(ContentKind.CODE, _CONFIG), CodeChunker)
        assert isinstance(build_chunker(ContentKind.DOCUMENT, _CONFIG), OffsetChunker)
        assert isinstance(build_chunker(ContentKind.PDF_PAGE, _CONFIG), OffsetChunker)

    async def test_output_order_preserved_despite_out_of_order_completion(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Chunk order matches ``raw.units`` even when the slower unit is first."""
        delays = {"unit-0-slow": 0.05, "unit-1-fast": 0.0}

        class _DelayedChunker:
            def chunk_unit(self, unit: RawUnit) -> tuple[ChunkPiece, ...]:
                time.sleep(delays[unit.text])
                return (
                    ChunkPiece(
                        text=unit.text,
                        locator=WebLocator(
                            url=NotBlankStr("https://x.test"),
                            char_start=0,
                            char_end=len(unit.text),
                        ),
                    ),
                )

        monkeypatch.setattr(
            "synthorg.knowledge.chunking.factory.build_chunker",
            lambda content_kind, config: _DelayedChunker(),
        )
        raw = RawDocument(
            source_id=NotBlankStr("src-order"),
            source_type=SourceType.PDF,
            uri=NotBlankStr("corpus/x.pdf"),
            title="X",
            content_hash="a" * 64,
            units=(
                _doc_unit("unit-0-slow"),
                _doc_unit("unit-1-fast"),
            ),
        )
        chunks = await chunk_raw_document(raw, config=_CONFIG)
        assert [c.text for c in chunks] == ["unit-0-slow", "unit-1-fast"]

    async def test_prefetch_called_once_with_deduplicated_languages(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "tree_sitter_language_pack.has_language",
            lambda name: True,
        )
        monkeypatch.setattr(
            "tree_sitter_language_pack.prefetch",
            lambda languages: calls.append(sorted(languages)),
        )
        raw = RawDocument(
            source_id=NotBlankStr("src-lang"),
            source_type=SourceType.REPO,
            uri=NotBlankStr("repo@main"),
            title="X",
            content_hash="a" * 64,
            units=(
                _code_unit("def a(): pass\n", path="a.py"),
                _code_unit("def b(): pass\n", path="b.py"),
                _code_unit("func c() {}\n", path="c.go"),
            ),
        )
        await chunk_raw_document(raw, config=_CONFIG)
        assert calls == [["go", "python"]]

    async def test_prefetch_happens_before_chunking(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        call_log: list[str] = []
        monkeypatch.setattr(
            "tree_sitter_language_pack.has_language",
            lambda name: True,
        )
        monkeypatch.setattr(
            "tree_sitter_language_pack.prefetch",
            lambda languages: call_log.append("prefetch"),
        )
        real_chunk_unit = CodeChunker.chunk_unit

        def logging_chunk_unit(
            self: CodeChunker,
            unit: RawUnit,
        ) -> tuple[ChunkPiece, ...]:
            call_log.append("chunk")
            return real_chunk_unit(self, unit)

        monkeypatch.setattr(CodeChunker, "chunk_unit", logging_chunk_unit)
        raw = RawDocument(
            source_id=NotBlankStr("src-order2"),
            source_type=SourceType.REPO,
            uri=NotBlankStr("repo@main"),
            title="X",
            content_hash="a" * 64,
            units=(_code_unit("def a(): pass\n"),),
        )
        await chunk_raw_document(raw, config=_CONFIG)
        assert call_log[0] == "prefetch"

    async def test_child_domain_error_propagates_unwrapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A KnowledgeError from a chunk task surfaces unwrapped, not as a group.

        The ingest caller dispatches on the bare ``KnowledgeError`` type,
        so the ``TaskGroup``'s ``ExceptionGroup`` must be unwrapped.
        """

        class _FailingChunker:
            def chunk_unit(self, unit: RawUnit) -> tuple[ChunkPiece, ...]:
                raise KnowledgeDependencyError

        monkeypatch.setattr(
            "synthorg.knowledge.chunking.factory.build_chunker",
            lambda content_kind, config: _FailingChunker(),
        )
        raw = RawDocument(
            source_id=NotBlankStr("src-fail"),
            source_type=SourceType.PDF,
            uri=NotBlankStr("corpus/x.pdf"),
            title="X",
            content_hash="a" * 64,
            units=(_doc_unit("boom"),),
        )
        with pytest.raises(KnowledgeDependencyError):
            await chunk_raw_document(raw, config=_CONFIG)

    async def test_child_critical_error_propagates_unwrapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A RecursionError from a chunk task must reach the caller unwrapped.

        A critical error demoted into a generic ``ExceptionGroup`` would
        defeat the ingest caller's ``except MemoryError, RecursionError``
        re-raise guard.
        """

        class _CriticalChunker:
            def chunk_unit(self, unit: RawUnit) -> tuple[ChunkPiece, ...]:
                raise RecursionError

        monkeypatch.setattr(
            "synthorg.knowledge.chunking.factory.build_chunker",
            lambda content_kind, config: _CriticalChunker(),
        )
        raw = RawDocument(
            source_id=NotBlankStr("src-crit"),
            source_type=SourceType.PDF,
            uri=NotBlankStr("corpus/x.pdf"),
            title="X",
            content_hash="a" * 64,
            units=(_doc_unit("boom"),),
        )
        with pytest.raises(RecursionError):
            await chunk_raw_document(raw, config=_CONFIG)

    async def test_unsupported_language_alongside_supported_degrades_gracefully(
        self,
    ) -> None:
        """A batch mixing an unsupported and a supported language chunks both.

        ``c_sharp`` is mapped from ``.cs`` but not actually provided by
        the installed tree-sitter-language-pack; it must degrade to a
        line-window chunk rather than aborting the whole batch (which
        would also prevent the ``.py`` unit's grammar from being used).
        """
        raw = RawDocument(
            source_id=NotBlankStr("src-mixed"),
            source_type=SourceType.REPO,
            uri=NotBlankStr("repo@main"),
            title="X",
            content_hash="a" * 64,
            units=(
                _code_unit("class Foo {}\n", path="Program.cs"),
                _code_unit("def foo():\n    return 1\n", path="a.py"),
            ),
        )
        chunks = await chunk_raw_document(raw, config=_CONFIG)
        texts = {c.text for c in chunks}
        assert any("class Foo" in t for t in texts)
        assert any("def foo" in t for t in texts)
