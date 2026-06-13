"""AST-aware code chunker built on tree-sitter.

Splits a source file at top-level definition boundaries (functions,
classes, methods, structs, ...) so each chunk is a coherent unit with a
line-span :class:`CodeLocator` and the enclosing symbol name. Runs of
module-level statements (imports, constants) pack together. Languages
with no bundled grammar, or an unknown file extension, degrade to a
deterministic line-window split.

``tree-sitter`` and ``tree-sitter-language-pack`` are optional (the
``synthorg[knowledge]`` extra); their absence raises
:class:`KnowledgeDependencyError` with install guidance.
"""

import functools
from typing import TYPE_CHECKING

from synthorg.core.text_estimation import DEFAULT_CHAR_PER_TOKEN
from synthorg.knowledge.chunking.protocol import ChunkPiece
from synthorg.knowledge.constants import (
    KNOWLEDGE_CHUNK_MAX_TOKENS,
    KNOWLEDGE_CHUNK_TARGET_TOKENS,
)
from synthorg.knowledge.errors import KnowledgeDependencyError
from synthorg.knowledge.models import CodeLocator, RawUnit

if TYPE_CHECKING:
    import tree_sitter

# Maps file extension to a tree-sitter-language-pack grammar name. Kept
# small and obvious; unknown extensions fall back to line windows.
_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
}

# Substrings that mark a node type as a top-level definition boundary
# across languages (function_definition, class_declaration, impl_item,
# method_declaration, struct_item, interface_declaration, trait_item...).
_DEFINITION_MARKERS: tuple[str, ...] = (
    "function",
    "class",
    "method",
    "struct",
    "impl",
    "interface",
    "trait",
    "module",
    "enum",
)

_MAX_CHARS: int = KNOWLEDGE_CHUNK_MAX_TOKENS * DEFAULT_CHAR_PER_TOKEN
_TARGET_CHARS: int = KNOWLEDGE_CHUNK_TARGET_TOKENS * DEFAULT_CHAR_PER_TOKEN


def _language_for(path: str) -> str | None:
    """Return the grammar name for *path*'s extension, or None."""
    lowered = path.lower()
    for ext, language in _EXTENSION_LANGUAGE.items():
        if lowered.endswith(ext):
            return language
    return None


@functools.cache
def _cached_grammar(language: str) -> tree_sitter.Language | None:
    """Load a tree-sitter grammar once per process; None when absent.

    ``tree_sitter_language_pack.get_language`` loads a compiled grammar
    binary from disk on every call. The knowledge-ingestion path chunks
    many units, so memoising the grammar avoids reloading the same binary
    for each one -- a redundant cost that, under heavy parallel load, can
    stretch a single cold load into a multi-second stall. Grammars are
    immutable metadata, so sharing one instance across calls (and threads)
    is safe; a fresh ``Parser`` is still built per call by ``_load_parser``.

    Returns:
        The cached ``tree_sitter.Language``, or ``None`` when the grammar
        for ``language`` is absent.

    Raises:
        KnowledgeDependencyError: When the ``tree-sitter`` extras are not
            installed.
    """
    try:
        from tree_sitter_language_pack import get_language  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "Code chunking needs the 'tree-sitter' extras. Install with "
            "`pip install synthorg[knowledge]`."
        )
        raise KnowledgeDependencyError(msg) from exc
    try:
        return get_language(language)
    except LookupError, OSError, ValueError:
        return None


def _load_parser(language: str) -> tree_sitter.Parser | None:
    """Build a tree-sitter parser from a cached grammar; None when absent.

    Uses the standard ``tree_sitter.Parser`` + ``get_language`` path
    (the tree-sitter Python API) rather than the language pack's bundled
    fast-binding, whose ``Tree`` / ``Node`` surface differs. The grammar
    load is memoised by :func:`_cached_grammar`; a fresh ``Parser`` is
    built per call so parsing state is never shared across threads.

    Returns:
        A configured ``tree_sitter.Parser``, or ``None`` when the grammar
        for ``language`` is absent.

    Raises:
        KnowledgeDependencyError: When the ``tree-sitter`` extras are not
            installed.
    """
    try:
        from tree_sitter import Parser  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "Code chunking needs the 'tree-sitter' extras. Install with "
            "`pip install synthorg[knowledge]`."
        )
        raise KnowledgeDependencyError(msg) from exc
    grammar = _cached_grammar(language)
    if grammar is None:
        return None
    return Parser(grammar)


class CodeChunker:
    """Tree-sitter chunker that splits code at definition boundaries."""

    def chunk_unit(self, unit: RawUnit) -> tuple[ChunkPiece, ...]:
        """Split a source file into definition / module-level chunks.

        Returns:
            The chunk pieces for the unit, or an empty tuple for blank
            text.

        Raises:
            TypeError: When ``unit.locator`` is not a ``CodeLocator``.
        """
        if not isinstance(unit.locator, CodeLocator):
            msg = (
                f"CodeChunker requires a CodeLocator; got {type(unit.locator).__name__}"
            )
            raise TypeError(msg)
        if not unit.text.strip():
            return ()
        path = unit.locator.path
        language = _language_for(path)
        parser = _load_parser(language) if language is not None else None
        lines = unit.text.split("\n")
        if parser is None:
            return self._line_windows(path=path, lines=lines)
        return self._ast_chunks(parser=parser, path=path, text=unit.text, lines=lines)

    def _ast_chunks(
        self,
        *,
        parser: tree_sitter.Parser,
        path: str,
        text: str,
        lines: list[str],
    ) -> tuple[ChunkPiece, ...]:
        """Walk top-level nodes, emitting definition + module-level chunks.

        Returns:
            The chunk pieces for the parsed tree: one per top-level
            definition plus packed module-level runs.
        """
        tree = parser.parse(text.encode("utf-8"))
        pieces: list[ChunkPiece] = []
        buffer: list[int] = []  # 0-indexed line numbers of pending module-level run

        def flush_buffer() -> None:
            if not buffer:
                return
            self._emit_lines(
                pieces, path=path, lines=lines, start=buffer[0], end=buffer[-1]
            )
            buffer.clear()

        for child in tree.root_node.named_children:
            start_row = int(child.start_point[0])
            end_row = int(child.end_point[0])
            if _is_definition(child.type):
                flush_buffer()
                self._emit_lines(
                    pieces,
                    path=path,
                    lines=lines,
                    start=start_row,
                    end=end_row,
                    symbol=_node_symbol(child),
                )
            else:
                buffer.extend(range(start_row, end_row + 1))
                run_text = "\n".join(lines[buffer[0] : buffer[-1] + 1])
                if len(run_text) >= _TARGET_CHARS:
                    flush_buffer()
        flush_buffer()
        if not pieces:
            return self._line_windows(path=path, lines=lines)
        return tuple(pieces)

    def _emit_lines(  # noqa: PLR0913 -- cohesive line-range emit params
        self,
        pieces: list[ChunkPiece],
        *,
        path: str,
        lines: list[str],
        start: int,
        end: int,
        symbol: str | None = None,
    ) -> None:
        """Emit one or more chunk pieces for the inclusive line range.

        Single chunks at or under ``_MAX_CHARS`` are emitted as one
        piece; otherwise lines are packed greedily into successive
        chunks so each chunk is as large as possible without exceeding
        the hard character budget. (An average-line-length window would
        overflow on non-uniform line widths, e.g. a generated file with
        one very long line, and silently truncate downstream embeddings.)
        """
        text = "\n".join(lines[start : end + 1])
        if not text.strip():
            return
        if len(text) <= _MAX_CHARS:
            pieces.append(
                _code_piece(
                    text=text,
                    path=path,
                    line_start=start + 1,
                    line_end=end + 1,
                    symbol=symbol,
                )
            )
            return
        win_start = start
        win_chars = 0
        for i in range(start, end + 1):
            # Add 1 for the newline that ``\n``.join inserts between lines.
            line_len = len(lines[i]) + 1
            if win_chars > 0 and win_chars + line_len > _MAX_CHARS:
                self._flush_window(
                    pieces,
                    path=path,
                    lines=lines,
                    win_start=win_start,
                    win_end=i - 1,
                    symbol=symbol,
                )
                win_start = i
                win_chars = line_len
            else:
                win_chars += line_len
        if win_start <= end:
            self._flush_window(
                pieces,
                path=path,
                lines=lines,
                win_start=win_start,
                win_end=end,
                symbol=symbol,
            )

    def _flush_window(  # noqa: PLR0913 -- cohesive window-flush params
        self,
        pieces: list[ChunkPiece],
        *,
        path: str,
        lines: list[str],
        win_start: int,
        win_end: int,
        symbol: str | None,
    ) -> None:
        """Append one or more pieces for the inclusive line window.

        A line longer than ``_MAX_CHARS`` (generated code, minified
        bundle, single-line config) can land in the window with
        ``win_chars == 0`` and slip past the loop's overflow check, so
        the hard cap is enforced HERE: text under the cap is emitted as
        a single piece; text over the cap is character-split into
        ``_MAX_CHARS``-sized segments. Splitting at character granularity
        is a deliberate tradeoff -- the alternative (refusing to chunk
        oversized content) would silently drop the source from the
        corpus, which is worse than a coarse line-locator citation.
        """
        chunk_text = "\n".join(lines[win_start : win_end + 1])
        if not chunk_text.strip():
            return
        if len(chunk_text) <= _MAX_CHARS:
            pieces.append(
                _code_piece(
                    text=chunk_text,
                    path=path,
                    line_start=win_start + 1,
                    line_end=win_end + 1,
                    symbol=symbol,
                )
            )
            return
        for offset in range(0, len(chunk_text), _MAX_CHARS):
            segment = chunk_text[offset : offset + _MAX_CHARS]
            if not segment.strip():
                continue
            pieces.append(
                _code_piece(
                    text=segment,
                    path=path,
                    line_start=win_start + 1,
                    line_end=win_end + 1,
                    symbol=symbol,
                )
            )

    def _line_windows(self, *, path: str, lines: list[str]) -> tuple[ChunkPiece, ...]:
        """Fallback: split into fixed line windows under the char budget.

        Returns:
            The chunk pieces produced by fixed line-window splitting.
        """
        pieces: list[ChunkPiece] = []
        self._emit_lines(pieces, path=path, lines=lines, start=0, end=len(lines) - 1)
        return tuple(pieces)


def _is_definition(node_type: str) -> bool:
    return any(marker in node_type for marker in _DEFINITION_MARKERS)


def _node_symbol(node: tree_sitter.Node) -> str | None:
    """Extract a definition's name via the ``name`` field, if present.

    Returns:
        The decoded definition name, or ``None`` when the node has no
        ``name`` field or it is empty.
    """
    name_node = node.child_by_field_name("name")
    if name_node is None or name_node.text is None:
        return None
    symbol = name_node.text.decode("utf-8", errors="replace").strip()
    return symbol or None


def _code_piece(
    *,
    text: str,
    path: str,
    line_start: int,
    line_end: int,
    symbol: str | None,
) -> ChunkPiece:
    from synthorg.core.types import NotBlankStr  # noqa: PLC0415

    # ast_path mirrors the symbol for top-level definitions; nested
    # qualification is a future refinement.
    symbol_value = NotBlankStr(symbol) if symbol else None
    return ChunkPiece(
        text=text,
        locator=CodeLocator(
            path=NotBlankStr(path),
            line_start=line_start,
            line_end=line_end,
            symbol=symbol_value,
            ast_path=symbol_value,
        ),
    )
