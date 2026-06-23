"""Lightweight lexical text-similarity helpers.

Consolidates the ad-hoc ``set(text.lower().split())`` tokenisation and
the overlap / cosine ratios that were re-derived at the ontology
divergence-warning, ontology drift, engine semantic-drift, and memory
ranking sites. These are deliberately dependency-free bag-of-words
measures: callers that need embeddings use the dedicated similarity
providers, not this module.
"""

import math
import re
from typing import Final

_WORD_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"\w+")


def split_word_chars(text: str) -> list[str]:
    """Lowercase *text* and extract word-character runs in source order.

    Unlike :func:`split_words` (whitespace split), this strips
    punctuation so ``"hello, world!"`` yields ``["hello", "world"]``.
    Use when membership should ignore surrounding punctuation.

    Args:
        text: Raw input string.

    Returns:
        Lowercased word-character tokens in source order.
    """
    return _WORD_CHARS_RE.findall(text.lower())


def tokenize_word_chars(text: str, *, min_length: int = 1) -> frozenset[str]:
    """Distinct lowercased word-character tokens, dropping short ones.

    Args:
        text: Raw input string.
        min_length: Minimum token length to retain (default keeps all).

    Returns:
        Distinct lowercased word-character tokens of at least
        ``min_length`` characters.
    """
    return frozenset(t for t in split_word_chars(text) if len(t) >= min_length)


def split_words(text: str) -> list[str]:
    """Lowercase and whitespace-split *text* into an ordered word list.

    Order-preserving; use when sequence matters (e.g. n-grams). For
    membership / overlap use :func:`tokenize_words`.

    Args:
        text: Raw input string.

    Returns:
        Lowercased whitespace-delimited tokens in source order.
    """
    return text.lower().split()


def tokenize_words(text: str) -> frozenset[str]:
    """Return the set of lowercased whitespace-delimited tokens.

    Args:
        text: Raw input string.

    Returns:
        Distinct lowercased tokens.
    """
    return frozenset(split_words(text))


def word_overlap(
    a: frozenset[str],
    b: frozenset[str],
    *,
    empty_b: float = 0.0,
) -> float:
    """Fraction of *b*'s tokens that also appear in *a*.

    Asymmetric by design (``|a & b| / |b|``): the existing callers ask
    "how much of the reference text *b* is covered by *a*".

    Args:
        a: Candidate token set.
        b: Reference token set the ratio is normalised against.
        empty_b: Value returned when *b* is empty. Defaults to ``0.0`` so
            an empty reference never yields a misleading perfect score;
            supersession passes ``1.0`` (an absent reference is vacuously
            covered).

    Returns:
        Overlap ratio in ``[0.0, 1.0]``.

    Raises:
        ValueError: When ``empty_b`` falls outside ``[0.0, 1.0]`` and so
            would break the documented return range.
    """
    if not 0.0 <= empty_b <= 1.0:
        msg = f"empty_b must be within [0.0, 1.0], got {empty_b}"
        raise ValueError(msg)
    if not b:
        return empty_b
    return len(a & b) / len(b)


def cosine_word_similarity(text_a: str, text_b: str) -> float:
    """Set-cosine similarity of the two texts' token bags.

    ``|A & B| / sqrt(|A| * |B|)``. Returns ``0.0`` when either side has
    no tokens. Treats each text as a binary bag of words (term counts
    are not weighted), matching the prior engine semantic-drift
    behaviour.

    Args:
        text_a: First input string.
        text_b: Second input string.

    Returns:
        Cosine similarity in ``[0.0, 1.0]``.
    """
    tokens_a = tokenize_words(text_a)
    tokens_b = tokenize_words(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    denom = math.sqrt(len(tokens_a) * len(tokens_b))
    if denom == 0.0:
        return 0.0
    return len(tokens_a & tokens_b) / denom
