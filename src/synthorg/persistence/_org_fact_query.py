# module-kind: code
"""Query-term extraction for org-fact text search.

The org-fact store is fused into a working agent's recall alongside its
personal memories, and that recall composes its query from the whole work
context (task title, objective, role, department), so the query text
reaching the store is a sentence, not a keyword. Matching such a query as
one ``content LIKE %<whole sentence>%`` substring never matches a real
fact, so the org layer would silently never reach the prompt.

This module owns the one tokenisation both persistence backends share, so
SQLite and Postgres extract identical terms from identical text and the
dual-backend conformance suite holds them to one ranking contract rather
than to two implementations that merely resemble each other.
"""

from typing import Final

#: Terms shorter than this carry no retrieval signal (``a``, ``to``) and,
#: matched as substrings, would pull in almost every fact.
_MIN_TERM_LENGTH: Final[int] = 3

#: Cap on distinct terms taken from one query. A composed query rarely
#: carries more genuine signal than this, and each term adds a clause to
#: the WHERE and the ranking expression, so the cap bounds the SQL size.
_MAX_TERMS: Final[int] = 12

#: High-frequency words that match almost any fact as substrings and so
#: only dilute term-match ranking. Deliberately small: precision comes
#: from the min-length floor and the match-count ranking, not from an
#: exhaustive stop list.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "not",
        "but",
        "you",
        "your",
        "our",
        "its",
        "into",
        "then",
        "than",
        "them",
        "they",
        "there",
        "here",
        "what",
        "when",
        "which",
        "will",
        "would",
        "should",
        "could",
    }
)


def org_query_terms(text: str) -> tuple[str, ...]:
    """Extract the distinct salient search terms from *text*.

    Lowercases, splits on any non-alphanumeric run, drops terms below the
    minimum length and common stopwords, de-duplicates while preserving
    first-seen order, and caps the count. Order is preserved so a caller
    that truncates keeps the leading (usually most salient) terms.

    Returns:
        The distinct query terms, at most :data:`_MAX_TERMS`; empty when
        *text* carries no term worth matching.
    """
    seen: dict[str, None] = {}
    token: list[str] = []
    for char in text:
        if char.isalnum():
            token.append(char.lower())
            continue
        _keep(token, seen)
        token.clear()
    _keep(token, seen)
    return tuple(seen)[:_MAX_TERMS]


def _keep(token: list[str], seen: dict[str, None]) -> None:
    """Record the accumulated *token* as a term when it qualifies."""
    if len(token) < _MIN_TERM_LENGTH:
        return
    term = "".join(token)
    if term in _STOPWORDS:
        return
    seen.setdefault(term, None)


def like_contains_pattern(value: str) -> str:
    r"""Return a ``LIKE ... ESCAPE '\'`` pattern matching *value* as a substring.

    The LIKE metacharacters ``%`` and ``_`` and the escape char itself are
    escaped so a term carrying one matches literally rather than as a
    wildcard. Both backends use ``ESCAPE '\'``, so the pattern is portable.

    Returns:
        The wrapped ``%value%`` pattern with metacharacters escaped.
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def build_term_match_sql(
    terms: tuple[str, ...],
    *,
    placeholder: str,
    int_cast: str,
) -> tuple[str, str, list[str]]:
    """Build the shared WHERE + ORDER SQL for a term-based fact search.

    Both backends call this with the same *terms* so their generated SQL is
    identical bar the placeholder token and the boolean-to-integer cast:
    the fusion recall path then ranks facts identically on SQLite and
    Postgres, which is the property the dual-backend conformance suite pins.

    Args:
        terms: The distinct query terms (must be non-empty; the caller
            handles the no-term literal-substring fallback).
        placeholder: The backend's positional placeholder (``?`` or ``%s``).
        int_cast: Suffix casting a boolean LIKE to an integer for summation
            (``""`` on SQLite, ``"::int"`` on Postgres).

    Returns:
        A ``(where_fragment, order_fragment, patterns)`` triple. Each term's
        LIKE pattern appears once in *patterns*; the caller binds it for the
        WHERE clause and again, in the same order, for the ORDER expression.

    Note:
        Each term's leading-wildcard ``LIKE`` is evaluated twice per row (once
        in the WHERE, once in the ORDER match-count) and cannot use an index,
        so a query costs up to ``2 * len(terms)`` scans per row, bounded by
        :data:`_MAX_TERMS`. That is acceptable for the curated, low-cardinality
        org-fact store this backs; if ``org_facts_snapshot`` ever grew large a
        trigram / FTS index (kept in dual-backend parity) would be warranted.
    """
    like = f"LOWER(content) LIKE {placeholder} ESCAPE '\\'"
    where_fragment = "(" + " OR ".join(like for _ in terms) + ")"
    match_count = " + ".join(f"({like}){int_cast}" for _ in terms)
    order_fragment = (
        f"({match_count}) DESC, LENGTH(content) ASC, created_at DESC, fact_id ASC"
    )
    patterns = [like_contains_pattern(term) for term in terms]
    return where_fragment, order_fragment, patterns
