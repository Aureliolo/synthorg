"""Okapi BM25 scoring over posting lists from the SQL inverted index.

Scoring lives here, beside the RRF and MMR ranking code, rather than in
SQL: the formula is identical for both backends, so keeping it in one
place means the SQLite and Postgres repositories differ only in how they
fetch rows, never in how they rank them. It also keeps the persistence
layer free of ranking policy.

The formula is the standard Okapi BM25::

    score(d, q) = sum over terms t in q of
        idf(t) * (tf(t, d) * (k1 + 1))
        / (tf(t, d) + k1 * (1 - b + b * len(d) / avg_len))

    idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

``k1`` and ``b`` take the values the literature treats as defaults.
"""

import math
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.memory.sparse import BM25Tokenizer

# Term-frequency saturation. Higher values let repeated terms keep
# adding score for longer; 1.2 is the long-standing Okapi default.
BM25_K1: Final[float] = 1.2
# Length normalisation strength. 0.0 ignores document length entirely,
# 1.0 normalises fully; 0.75 is the long-standing Okapi default.
BM25_B: Final[float] = 0.75

_tokenizer: Final[BM25Tokenizer] = BM25Tokenizer()


def tokenize_for_index(text: str) -> tuple[NotBlankStr, ...]:
    """Tokenise text for writing into the inverted index.

    Shared by the write path and the query path so an entry is indexed
    under exactly the terms a query can match. Divergence here would
    silently break lexical recall.

    Args:
        text: Raw text to tokenise.

    Returns:
        Lowercase, stop-word-filtered tokens in order.
    """
    return tuple(NotBlankStr(t) for t in _tokenizer.tokenize(text))


def term_frequencies(text: str) -> dict[NotBlankStr, int]:
    """Count term occurrences for one document.

    Args:
        text: Raw document text.

    Returns:
        Mapping of term to occurrence count. Empty when the text has no
        indexable tokens.
    """
    counts: dict[NotBlankStr, int] = {}
    for token in tokenize_for_index(text):
        counts[token] = counts.get(token, 0) + 1
    return counts


def inverse_document_frequency(*, doc_count: int, doc_frequency: int) -> float:
    """Compute the BM25 IDF for one term.

    Args:
        doc_count: Number of documents in the filtered corpus.
        doc_frequency: Number of those documents containing the term.

    Returns:
        The IDF weight. Non-negative: the ``1 +`` inside the logarithm
        keeps a term appearing in every document near zero (exactly zero
        only as the corpus grows) rather than negative, so a ubiquitous
        term cannot penalise a document.
    """
    numerator = doc_count - doc_frequency + 0.5
    denominator = doc_frequency + 0.5
    return math.log(1.0 + numerator / denominator)


def score_document(
    *,
    matched: tuple[tuple[NotBlankStr, int], ...],
    doc_length: int,
    doc_count: int,
    doc_frequencies: dict[NotBlankStr, int],
    avg_length: float,
) -> float:
    """Score one document against the query terms it matched.

    Args:
        matched: ``(term, term_frequency)`` pairs for query terms present
            in this document.
        doc_length: Token count of this document.
        doc_count: Number of documents in the filtered corpus.
        doc_frequencies: Per-term document frequency across that corpus.
        avg_length: Mean token count across that corpus.

    Returns:
        The BM25 score. Zero when nothing matched or the corpus is
        empty.
    """
    if not matched or doc_count <= 0:
        return 0.0
    # A zero average length would divide by zero below; it can only
    # happen when every document indexed zero tokens, in which case
    # length normalisation is meaningless anyway.
    effective_avg = avg_length if avg_length > 0.0 else 1.0
    total = 0.0
    for term, frequency in matched:
        idf = inverse_document_frequency(
            doc_count=doc_count,
            doc_frequency=doc_frequencies.get(term, 0),
        )
        norm = 1.0 - BM25_B + BM25_B * (doc_length / effective_avg)
        total += idf * (frequency * (BM25_K1 + 1.0)) / (frequency + BM25_K1 * norm)
    return total


def normalise_scores(scores: tuple[float, ...]) -> tuple[float, ...]:
    """Min-max normalise raw BM25 scores into ``[0, 1]``.

    BM25 is unbounded above, but ``MemoryEntry.relevance_score`` is
    constrained to ``[0, 1]``. Normalisation is per result set, so the
    value is a within-set ranking signal and must not be compared across
    queries or treated as a calibrated relevance probability.

    Args:
        scores: Raw scores in rank order.

    Returns:
        Normalised scores in the same order. An all-equal input maps to
        all ``1.0`` rather than all ``0.0``, since every result is
        equally and maximally relevant within that set.
    """
    if not scores:
        return ()
    lowest = min(scores)
    highest = max(scores)
    if highest <= lowest:
        return tuple(1.0 for _ in scores)
    span = highest - lowest
    return tuple((score - lowest) / span for score in scores)
