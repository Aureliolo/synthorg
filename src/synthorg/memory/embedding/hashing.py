# module-kind: adapter
"""The built-in embedder: no model, no provider, no network.

Deterministic signed feature hashing over word tokens, L2-normalised.
Lexical rather than semantic: it matches texts that share vocabulary, not
texts that share meaning, so recall through it is materially weaker than
through any real embedding model. It exists so an operator with no
embedding model can still run, and so that choice is visible.

It is never a fallback. Nothing may substitute it for a model that failed
to load, a provider that went unreachable, an absent optional dependency,
or an unset setting: memory silently becoming lexical is precisely the
failure the memory health surface was built to expose, and a substitution
would reintroduce it one layer down. The only path here is an operator
choosing it, which ``check_no_silent_embedder_fallback.py`` enforces.

Serves both embedder ports from one implementation: the synchronous
``embed`` the meeting conflict detectors call, and the asynchronous
``embed_many`` the memory substrate calls.
"""

import hashlib
import re
from typing import Final

import numpy as np

#: Provider and model halves of the built-in binding. Real names, not
#: sentinels: every dispatch resolves an explicit ``(provider, model)``
#: pair, and the built-in is a provider that happens to need no network.
BUILTIN_EMBEDDER_PROVIDER: Final[str] = "builtin"
BUILTIN_EMBEDDER_MODEL: Final[str] = "hashing"

#: Provider-qualified reference, matching ``ProviderTextEmbedder.model_ref``.
BUILTIN_EMBEDDER_REF: Final[str] = (
    f"{BUILTIN_EMBEDDER_PROVIDER}/{BUILTIN_EMBEDDER_MODEL}"
)

#: Default bucket count. Sits under the 2000-dimension full-precision HNSW
#: ceiling, so the built-in is always indexable and can never be the reason
#: dense search degrades to an exact scan.
BUILTIN_EMBEDDER_DIMS: Final[int] = 1024

_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\w+")

#: Leading digest bytes selecting the bucket: 64 bits keeps the bucket
#: distribution flat for any width the store accepts.
_BUCKET_BYTES: Final[int] = 8

#: One further byte supplies the sign. It must not overlap the bucket
#: bytes. Deriving both from a single value makes the sign a function of
#: the bucket whenever the width is a power of two, so colliding tokens
#: always reinforce and never cancel, destroying the one property signed
#: feature hashing exists to provide.
_DIGEST_SIZE: Final[int] = _BUCKET_BYTES + 1


class HashingTextEmbedder:
    """Deterministic feature-hashing embedder over word tokens.

    Args:
        dims: Vector width (number of hash buckets).

    Raises:
        ValueError: If ``dims`` is below one.
    """

    def __init__(self, *, dims: int = BUILTIN_EMBEDDER_DIMS) -> None:
        if dims < 1:
            msg = "dims must be >= 1"
            raise ValueError(msg)
        self._dims = dims

    @property
    def dimensions(self) -> int:
        """Width of every vector this embedder produces."""
        return self._dims

    @property
    def model_ref(self) -> str:
        """The provider-qualified identifier for cost and log attribution."""
        return BUILTIN_EMBEDDER_REF

    def embed(self, text: str) -> tuple[float, ...]:
        """Hash *text* into an L2-normalised vector.

        Returns:
            The normalised embedding vector (all zeros for empty text).
        """
        vec = np.zeros(self._dims, dtype=np.float64)
        for token in _TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.blake2b(
                token.encode("utf-8"), digest_size=_DIGEST_SIZE
            ).digest()
            bucket = int.from_bytes(digest[:_BUCKET_BYTES], "big") % self._dims
            sign = 1.0 if digest[_BUCKET_BYTES] & 1 else -1.0
            vec[bucket] += sign
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return tuple(vec.tolist())
        return tuple((vec / norm).tolist())

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed a batch of texts, preserving input order.

        Args:
            texts: Texts to embed. An empty tuple returns an empty tuple.

        Returns:
            One vector per input text, in the same order.
        """
        return tuple(self.embed(text) for text in texts)


__all__ = [
    "BUILTIN_EMBEDDER_DIMS",
    "BUILTIN_EMBEDDER_MODEL",
    "BUILTIN_EMBEDDER_PROVIDER",
    "BUILTIN_EMBEDDER_REF",
    "HashingTextEmbedder",
]
