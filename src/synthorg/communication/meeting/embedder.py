# module-kind: code
"""Text embedding for the embedding conflict detector.

Defines the :class:`TextEmbedder` protocol and builds the one
implementation the detectors use: :class:`HashingTextEmbedder`, a
dependency-free deterministic feature-hashing embedder (numpy only).
It is lexical rather than semantic, and it is chosen here rather than
offered as one option among several.

There is exactly one embedding model an operator names,
``memory.embedder_model``, and it is not this: that binding dispatches
to a provider over HTTP and serves durable agent memory. A second,
locally-loaded embedder selected from meeting configuration would be a
second choice surface for the same decision, and the backend image
carries neither torch nor sentence-transformers, so it could only fail
on a shipped deployment.

The hashing embedder is a primary choice for scoring scheduling
conflicts, never a fallback for a model that failed: nothing here
substitutes one embedder for another.
"""

from typing import Final, Protocol, runtime_checkable

import numpy as np

from synthorg.memory.embedding.hashing import HashingTextEmbedder

#: Bucket count for conflict scoring, narrower than the memory default.
#: These vectors are compared in-process and never indexed, so the width
#: buys nothing against a vector store's ceiling and only costs time over
#: a corpus of meeting titles.
_CONFLICT_HASH_DIMS: Final[int] = 256


@runtime_checkable
class TextEmbedder(Protocol):
    """Maps text to a fixed-width numeric vector for similarity scoring."""

    def embed(self, text: str) -> tuple[float, ...]:
        """Embed *text* into a fixed-width vector.

        Returns:
            The embedding vector as a tuple of floats.
        """
        ...


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Cosine similarity of two vectors.

    Args:
        left: First embedding vector.
        right: Second embedding vector.

    Returns:
        The cosine similarity in ``[-1.0, 1.0]``; ``0.0`` when either
        vector is degenerate (zero norm) so a missing embedding never
        reads as agreement.
    """
    vec_left = np.asarray(left, dtype=np.float64)
    vec_right = np.asarray(right, dtype=np.float64)
    norm_left = float(np.linalg.norm(vec_left))
    norm_right = float(np.linalg.norm(vec_right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return float(np.dot(vec_left, vec_right) / (norm_left * norm_right))


def build_text_embedder() -> TextEmbedder:
    """Build the embedder the conflict detectors score positions with.

    Returns:
        A :class:`HashingTextEmbedder` at the conflict-scoring width.
    """
    return HashingTextEmbedder(dims=_CONFLICT_HASH_DIMS)
