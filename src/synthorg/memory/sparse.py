"""BM25 sparse encoder for hybrid search.

Provides a hash-based BM25 tokenizer that converts text into sparse
vectors suitable for Qdrant's sparse vector fields.  Uses murmurhash3
for vocabulary-free token-to-index mapping; Qdrant's ``Modifier.IDF``
handles IDF scoring server-side, so only term frequencies are stored.
"""

import re
import unicodedata
from collections import Counter
from typing import Final, Self

import mmh3
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.observability import get_logger

logger = get_logger(__name__)

# Minimal English stop words -- kept small to avoid over-filtering
# domain-specific content.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
    }
)

_TOKEN_SPLIT_RE = re.compile(r"[\W_]+")
_MIN_INDICES_FOR_SORT_CHECK: Final[int] = 2


class SparseVector(BaseModel):
    """Sparse vector representation for BM25 term frequencies.

    Indices are murmurhash3-derived token identifiers; values are
    raw term frequency counts.  Both tuples must have equal length,
    indices must be sorted ascending, and values must be positive.

    Attributes:
        indices: Sorted token hash indices (non-negative).
        values: Corresponding term frequency values (positive).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    indices: tuple[int, ...] = Field(
        default=(),
        description="Sorted token hash indices",
    )
    values: tuple[float, ...] = Field(
        default=(),
        description="Term frequency values",
    )

    @model_validator(mode="after")
    def _validate_structure(self) -> Self:
        """Validate length match, sorted indices, non-negative, positive values."""
        if len(self.indices) != len(self.values):
            msg = (
                f"indices and values must have equal length, "
                f"got {len(self.indices)} and {len(self.values)}"
            )
            raise ValueError(msg)
        if self.values and any(v <= 0 for v in self.values):
            msg = "values must be positive (> 0)"
            raise ValueError(msg)
        if self.indices and any(idx < 0 for idx in self.indices):
            msg = "indices must be non-negative"
            raise ValueError(msg)
        if len(self.indices) >= _MIN_INDICES_FOR_SORT_CHECK and any(
            a >= b for a, b in zip(self.indices, self.indices[1:], strict=False)
        ):
            msg = "indices must be sorted in strictly ascending order"
            raise ValueError(msg)
        return self

    @property
    def is_empty(self) -> bool:
        """Whether the vector has no entries."""
        return len(self.indices) == 0


class BM25Tokenizer:
    """Hash-based BM25 tokenizer for sparse vector encoding.

    Tokenizes text into lowercase terms, optionally removes stop
    words, and encodes term frequencies as a sparse vector using
    murmurhash3 for deterministic vocabulary-free hashing.

    Args:
        remove_stop_words: Whether to exclude common English stop
            words from tokenization.  Defaults to ``True``.
    """

    __slots__ = ("_remove_stop_words",)

    def __init__(self, *, remove_stop_words: bool = True) -> None:
        self._remove_stop_words = remove_stop_words

    def tokenize(self, text: str) -> tuple[str, ...]:
        """Split text into lowercase tokens.

        Splits on non-word characters and underscores, lowercases
        all tokens, and optionally removes stop words.

        Args:
            text: Input text to tokenize.

        Returns:
            Tuple of lowercase tokens in original order.
        """
        if not text or not text.strip():
            return ()
        normalized = unicodedata.normalize("NFKC", text).casefold()
        raw_tokens = _TOKEN_SPLIT_RE.split(normalized)
        tokens = [t for t in raw_tokens if t]
        if self._remove_stop_words:
            tokens = [t for t in tokens if t not in _STOP_WORDS]
        return tuple(tokens)

    def encode(self, text: str) -> SparseVector:
        """Encode text as a BM25 sparse vector.

        Tokenizes, hashes each token to a 32-bit unsigned index via
        murmurhash3, counts term frequencies, and returns a sorted
        sparse vector.

        Args:
            text: Input text to encode.

        Returns:
            Sparse vector with sorted indices and TF values.
            Empty vector for empty or stop-word-only text.
        """
        tokens = self.tokenize(text)
        if not tokens:
            return SparseVector()

        tf_counts: Counter[int] = Counter()
        for token in tokens:
            # mmh3.hash returns signed 32-bit; mask to unsigned
            idx = mmh3.hash(token, signed=False)
            tf_counts[idx] += 1

        # Sort by index for consistent representation
        sorted_items = sorted(tf_counts.items())
        indices = tuple(idx for idx, _ in sorted_items)
        values = tuple(float(count) for _, count in sorted_items)

        return SparseVector(indices=indices, values=values)
