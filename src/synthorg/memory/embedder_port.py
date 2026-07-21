"""Async text-embedding port for the SQL-backed vector memory.

The vector backend embeds content on write and queries on read. It
depends on this structural port rather than a concrete embedder so the
provider-backed embedder, a local sentence-transformers embedder and a
deterministic test double are all interchangeable.

``embed_many`` is the primary method rather than a per-text ``embed``:
provider embedding endpoints are batch-oriented, and issuing one request
per memory would multiply latency and cost on the retrieval path.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextEmbedder(Protocol):
    """Turns text into dense vectors for similarity search."""

    @property
    def dimensions(self) -> int:
        """Length of every vector this embedder produces.

        The vector column is declared with a fixed width, so a change
        here invalidates the stored index and must be treated as a
        re-index, never as a silent widening.
        """
        ...

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed a batch of texts, preserving input order.

        Args:
            texts: Texts to embed. An empty tuple returns an empty tuple.

        Returns:
            One vector per input text, in the same order, each of length
            :attr:`dimensions`.

        Raises:
            MemoryEmbeddingError: If the embedding call fails.
        """
        ...
