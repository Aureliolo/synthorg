"""Deduplication strategies for retrieved items.

:class:`LexicalDeduplicator` is the deterministic, replay-friendly default:
it collapses items sharing a content hash, a canonical URL, or a high
token-shingle Jaccard overlap. :class:`EmbeddingDeduplicator` is the
pluggable alternative for semantic near-duplicates, driven by an injected
embedder.
"""

import math
import re
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable
from urllib.parse import urlsplit

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.research.constants import (
    RESEARCH_DEDUP_JACCARD_THRESHOLD,
    RESEARCH_DEDUP_SHINGLE_SIZE,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from synthorg.research.models import RetrievedItem

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Embeds texts into dense vectors for semantic deduplication."""

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Return one embedding vector per input text, in order."""
        ...


def _canonical_url(uri: str) -> str:
    """Normalise a URI for equality (drop scheme, query, fragment, slash).

    Only the host is case-folded (hosts are case-insensitive); the path is
    preserved verbatim because path segments are case-sensitive and distinct
    resources must not collapse into one another.

    Returns:
        The lower-cased host plus path with scheme, query, fragment, and
        trailing slash dropped.
    """
    raw = uri.strip()
    parts = urlsplit(raw)
    netloc = normalize_ascii_lowercase(parts.netloc)
    host_path = f"{netloc}{parts.path}".rstrip("/")
    return host_path or normalize_ascii_lowercase(raw)


def _shingles(text: str) -> frozenset[tuple[str, ...]]:
    """Return the set of word n-gram shingles for *text*."""
    tokens = _TOKEN_RE.findall(text.lower())
    width = RESEARCH_DEDUP_SHINGLE_SIZE
    if len(tokens) < width:
        return frozenset({tuple(tokens)}) if tokens else frozenset()
    return frozenset(
        tuple(tokens[i : i + width]) for i in range(len(tokens) - width + 1)
    )


def _jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    """Return the Jaccard similarity of two shingle sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Return the cosine similarity of two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keep_representatives(
    items: tuple[RetrievedItem, ...],
    is_duplicate: Callable[[RetrievedItem, RetrievedItem], bool],
) -> tuple[RetrievedItem, ...]:
    """Keep the highest-relevance item per duplicate cluster, in order.

    Items are considered in descending relevance so the representative kept
    for each cluster is its strongest member; the kept items are then
    returned in their original input order.

    Returns:
        One representative (the highest-relevance member) per duplicate
        cluster, in original input order.
    """
    order = sorted(
        range(len(items)),
        key=lambda i: (-items[i].relevance_score, i),
    )
    kept: list[int] = []
    for candidate in order:
        if any(is_duplicate(items[candidate], items[k]) for k in kept):
            continue
        kept.append(candidate)
    return tuple(items[i] for i in sorted(kept))


class LexicalDeduplicator:
    """Deterministic deduplication by hash, URL, and token overlap."""

    __slots__ = ("_threshold",)

    def __init__(self, *, threshold: float = RESEARCH_DEDUP_JACCARD_THRESHOLD) -> None:
        self._threshold = threshold

    async def dedupe(
        self,
        items: tuple[RetrievedItem, ...],
    ) -> tuple[RetrievedItem, ...]:
        """Return *items* with lexical near-duplicates collapsed."""
        shingle_cache = {item.ref_id: _shingles(item.snippet) for item in items}

        def is_duplicate(a: RetrievedItem, b: RetrievedItem) -> bool:
            if a.content_hash == b.content_hash:
                return True
            if _canonical_url(a.uri) == _canonical_url(b.uri):
                return True
            similarity = _jaccard(shingle_cache[a.ref_id], shingle_cache[b.ref_id])
            return similarity >= self._threshold

        return _keep_representatives(items, is_duplicate)


class EmbeddingDeduplicator:
    """Semantic deduplication via an injected embedder (cosine similarity)."""

    __slots__ = ("_embedder", "_threshold")

    def __init__(
        self,
        *,
        embedder: Embedder,
        threshold: float = RESEARCH_DEDUP_JACCARD_THRESHOLD,
    ) -> None:
        self._embedder = embedder
        self._threshold = threshold

    async def dedupe(
        self,
        items: tuple[RetrievedItem, ...],
    ) -> tuple[RetrievedItem, ...]:
        """Return *items* with semantic near-duplicates collapsed."""
        if not items:
            return ()
        vectors = await self._embedder.embed([item.snippet for item in items])
        by_ref = dict(zip((item.ref_id for item in items), vectors, strict=True))

        def is_duplicate(a: RetrievedItem, b: RetrievedItem) -> bool:
            if a.content_hash == b.content_hash:
                return True
            return _cosine(by_ref[a.ref_id], by_ref[b.ref_id]) >= self._threshold

        return _keep_representatives(items, is_duplicate)
