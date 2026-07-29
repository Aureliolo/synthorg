# module-kind: code
"""Text-embedding backends for the embedding conflict detector.

Defines the :class:`TextEmbedder` protocol plus two implementations
selected by the ``embedder_strategy`` discriminator:

* ``hashing`` (default): :class:`HashingTextEmbedder`, a dependency-free
  deterministic feature-hashing embedder (numpy only). Lexical rather
  than semantic, but always available and reproducible.
* ``sentence_transformer``: a real neural embedder behind the optional
  ``sentence-transformers`` extra; the factory translates a missing extra
  into :class:`MeetingEmbedderUnavailableError` for the detector's contract.

Both implementations live in ``memory.embedding``, the canonical home for
embedder adapters; this module selects between them for the detector.

Here the hashing embedder is a primary strategy for scoring scheduling
conflicts, not a fallback for a failed model: neither factory substitutes
one embedder for another, and a missing extra raises.
"""

from typing import Final, Protocol, runtime_checkable

import numpy as np

from synthorg.communication.meeting.errors import MeetingEmbedderUnavailableError
from synthorg.core.registry.strategy import StrategyRegistry
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


def _build_hashing(**_kwargs: object) -> TextEmbedder:
    """Build the hashing embedder (the dependency-free default).

    Returns:
        A :class:`HashingTextEmbedder` at the conflict-scoring width.
    """
    return HashingTextEmbedder(dims=_CONFLICT_HASH_DIMS)


def _build_sentence_transformer(**_kwargs: object) -> TextEmbedder:
    """Build the optional sentence-transformers embedder.

    Imports the shared adapter from ``memory.embedding`` so the SDK is
    bound at that boundary, not here, and translates a missing extra into
    the meeting-layer error the detector contract documents.

    Returns:
        The shared sentence-transformers embedder.

    Raises:
        MeetingEmbedderUnavailableError: When the ``sentence-transformers``
            extra is not installed.
    """
    from synthorg.memory.embedding.sentence_transformer import (  # noqa: PLC0415
        SentenceTransformerEmbedder,
    )
    from synthorg.memory.errors import (  # noqa: PLC0415
        MemoryEmbedderUnavailableError,
    )

    try:
        return SentenceTransformerEmbedder()
    except MemoryEmbedderUnavailableError as exc:
        raise MeetingEmbedderUnavailableError(str(exc)) from exc


_EMBEDDER_REGISTRY: StrategyRegistry[TextEmbedder] = StrategyRegistry(
    {
        "hashing": _build_hashing,
        "sentence_transformer": _build_sentence_transformer,
    },
    kind="text_embedder",
)


def build_text_embedder(strategy: str) -> TextEmbedder:
    """Build the text embedder for *strategy*.

    Args:
        strategy: The ``embedder_strategy`` discriminator (``hashing``
            or ``sentence_transformer``).

    Returns:
        The selected :class:`TextEmbedder`.

    Raises:
        StrategyFactoryNotFoundError: When *strategy* is unknown.
        MeetingEmbedderUnavailableError: When the sentence-transformers
            extra is required but absent.
    """
    return _EMBEDDER_REGISTRY.build(strategy)
