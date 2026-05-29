"""Shared helpers for retrieval-source adapters.

Centralises stable reference-id construction, snippet truncation, and the
positional relevance score used by sources whose providers do not return
their own ranking signal.
"""

from synthorg.research.constants import (
    RESEARCH_REF_ID_PREFIX,
    RESEARCH_SNIPPET_MAX_CHARS,
)


def make_ref_id(sub_query_index: int, position: int) -> str:
    """Return a run-unique reference id for one retrieved item.

    Sub-query indices are unique within a plan, so ``src-<index>-<pos>`` is
    globally unique across all sources in a run and is deterministic, which
    keeps a recorded run replayable.
    """
    return f"{RESEARCH_REF_ID_PREFIX}-{sub_query_index}-{position}"


def truncate_snippet(text: str) -> str:
    """Clamp source text to the snippet bound, leaving a non-empty result.

    Falls back to a placeholder when the source yields no text, so the
    non-empty :class:`SnippetText` constraint always holds.

    Returns:
        The stripped text truncated to the snippet bound, or a placeholder
        when the input is empty.
    """
    stripped = text.strip()
    if not stripped:
        return "(no excerpt available)"
    return stripped[:RESEARCH_SNIPPET_MAX_CHARS]


def positional_relevance(position: int, total: int) -> float:
    """Return a descending [0, 1] relevance score from list position.

    The first result scores 1.0 and later results decay linearly; used by
    providers (web / academic / code search) that return ranked results
    without explicit scores.
    """
    if total <= 0:
        return 0.0
    return max(0.0, (total - position) / total)
