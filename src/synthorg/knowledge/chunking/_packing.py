"""Shared paragraph-packing used by the document and PDF chunkers.

Splits text on blank-line paragraph boundaries and packs paragraphs into
char spans up to a target budget, hard-splitting any single paragraph
that exceeds the maximum. Returns ``(char_start, char_end)`` spans into
the original text so callers can attach precise offset locators.
"""

import re

from synthorg.knowledge.constants import (
    KNOWLEDGE_CHAR_PER_TOKEN_PROXY,
    KNOWLEDGE_CHUNK_MAX_TOKENS,
    KNOWLEDGE_CHUNK_TARGET_TOKENS,
)

_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def approx_tokens(text: str) -> int:
    """Approximate token count via the chars-per-token proxy.

    Returns:
        ``0`` for empty text, otherwise at least ``1`` token scaled by the
        chars-per-token proxy.
    """
    if not text:
        return 0
    return max(1, len(text) // KNOWLEDGE_CHAR_PER_TOKEN_PROXY)


def _max_chars() -> int:
    return KNOWLEDGE_CHUNK_MAX_TOKENS * KNOWLEDGE_CHAR_PER_TOKEN_PROXY


def _target_chars() -> int:
    return KNOWLEDGE_CHUNK_TARGET_TOKENS * KNOWLEDGE_CHAR_PER_TOKEN_PROXY


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of non-blank paragraphs in *text*."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _PARAGRAPH_RE.finditer(text):
        end = match.start()
        if text[cursor:end].strip():
            spans.append((cursor, end))
        cursor = match.end()
    if text[cursor:].strip():
        spans.append((cursor, len(text)))
    return spans


def _hard_split(start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Split an oversized span into fixed-width sub-spans.

    Returns:
        Consecutive ``(start, end)`` sub-spans of at most ``max_chars``
        covering ``[start, end)``.
    """
    pieces: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        pieces.append((pos, min(pos + max_chars, end)))
        pos += max_chars
    return pieces


def pack_text_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Pack *text* into ``(char_start, char_end)`` chunk spans.

    Paragraphs are merged up to the target budget; a paragraph larger
    than the hard maximum is split at fixed width. Whitespace-only text
    yields no spans.

    Returns:
        The packed ``(char_start, char_end)`` chunk spans, or an empty
        tuple for whitespace-only text.
    """
    if not text.strip():
        return ()
    target = _target_chars()
    max_chars = _max_chars()
    spans: list[tuple[int, int]] = []
    run_start: int | None = None
    run_end = 0
    for para_start, para_end in _paragraph_spans(text):
        if para_end - para_start > max_chars:
            if run_start is not None:
                spans.append((run_start, run_end))
                run_start = None
            spans.extend(_hard_split(para_start, para_end, max_chars))
            continue
        if run_start is None:
            run_start, run_end = para_start, para_end
        elif para_end - run_start <= target:
            run_end = para_end
        else:
            spans.append((run_start, run_end))
            run_start, run_end = para_start, para_end
    if run_start is not None:
        spans.append((run_start, run_end))
    return tuple(spans)
