"""Lightweight lexical text-similarity helpers.

Consolidates the ad-hoc ``set(text.lower().split())`` tokenisation and
the overlap / cosine ratios that were re-derived at the ontology
divergence-warning, ontology drift, engine semantic-drift, and memory
ranking sites. These are deliberately dependency-free bag-of-words
measures: callers that need embeddings use the dedicated similarity
providers, not this module.
"""

import math


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


def word_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Fraction of *b*'s tokens that also appear in *a*.

    Asymmetric by design (``|a & b| / |b|``): the existing callers ask
    "how much of the reference text *b* is covered by *a*". Returns
    ``0.0`` when *b* is empty so an empty reference never yields a
    misleading perfect score.

    Args:
        a: Candidate token set.
        b: Reference token set the ratio is normalised against.

    Returns:
        Overlap ratio in ``[0.0, 1.0]``.
    """
    if not b:
        return 0.0
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
