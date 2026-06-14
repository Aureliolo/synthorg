"""Shared token-count heuristic.

A single character-per-token approximation used wherever a cheap,
provider-agnostic token estimate is needed (prompt-budget guards, RAG
chunk packing, doc chunking). It lives in ``core`` so the engine, memory,
docs, knowledge, and project-brain subsystems share one constant and one
floor rule instead of each redefining ``len(text) // 4``.

This is an approximation, not a tokenizer: it deliberately avoids loading
a model-specific encoder so it stays cheap and dependency-free.
"""

from typing import Final

DEFAULT_CHAR_PER_TOKEN: Final[int] = 4
"""Characters-per-token divisor for the cheap length heuristic.

Four characters per token is the conventional rule-of-thumb that tracks
English byte-pair-encoded tokenizers closely enough for budget guards.
"""


def approx_tokens(text: str, *, chars_per_token: int = DEFAULT_CHAR_PER_TOKEN) -> int:
    """Approximate the token count of ``text`` from its length.

    Args:
        text: The text to estimate. Empty text estimates to ``0``.
        chars_per_token: Characters-per-token divisor. Defaults to
            :data:`DEFAULT_CHAR_PER_TOKEN`.

    Returns:
        ``0`` for empty text, otherwise ``max(1, len(text) //
        chars_per_token)`` so any non-empty text counts as at least one
        token.

    Raises:
        ValueError: If ``chars_per_token`` is below 1. A zero divisor
            would raise ``ZeroDivisionError`` and a negative one would
            produce a meaningless estimate.
    """
    if chars_per_token < 1:
        msg = "chars_per_token must be >= 1"
        raise ValueError(msg)
    if not text:
        return 0
    return max(1, len(text) // chars_per_token)
