# module-kind: code
"""Text-embedding backends for the embedding conflict detector.

Defines the :class:`TextEmbedder` protocol plus two implementations
selected by the ``embedder_strategy`` discriminator:

* ``hashing`` (default): :class:`HashingTextEmbedder`, a dependency-free
  deterministic feature-hashing embedder (numpy only). Lexical rather
  than semantic, but always available and reproducible.
* ``sentence_transformer``: a real neural embedder behind the optional
  ``sentence-transformers`` extra. The adapter lives in
  ``memory.embedding`` (the canonical home for the sentence-transformers
  integration); the factory here builds it and translates a missing extra
  into :class:`MeetingEmbedderUnavailableError` for the detector's contract.

The protocol lets operators swap a neural backend in without touching the
detector, while keeping the default install dependency-light.
"""

import hashlib
import re
from typing import Final, Protocol, runtime_checkable

import numpy as np

from synthorg.communication.meeting.errors import MeetingEmbedderUnavailableError
from synthorg.core.registry.strategy import StrategyRegistry

_HASH_DIMS: Final[int] = 256
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\w+")


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


class HashingTextEmbedder:
    """Dependency-free deterministic feature-hashing embedder.

    Tokenises on word boundaries and accumulates each token into a
    fixed-width vector via signed feature hashing, then L2-normalises.
    Lexical rather than semantic, but deterministic, fast, and free of
    heavy dependencies.

    Args:
        dims: Vector width (number of hash buckets).
    """

    def __init__(self, *, dims: int = _HASH_DIMS) -> None:
        if dims < 1:
            msg = "dims must be >= 1"
            raise ValueError(msg)
        self._dims = dims

    def embed(self, text: str) -> tuple[float, ...]:
        """Hash *text* into an L2-normalised vector.

        Returns:
            The normalised embedding vector (all zeros for empty text).
        """
        vec = np.zeros(self._dims, dtype=np.float64)
        for token in _TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            bucket = value % self._dims
            sign = 1.0 if (value >> 1) & 1 else -1.0
            vec[bucket] += sign
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return tuple(vec.tolist())
        return tuple((vec / norm).tolist())


def _build_hashing(**_kwargs: object) -> TextEmbedder:
    """Build the hashing embedder (the dependency-free default).

    Returns:
        A :class:`HashingTextEmbedder`.
    """
    return HashingTextEmbedder()


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
