# module-kind: adapter
"""Provider-backed text embedder for durable agent memory.

Satisfies :class:`TextEmbedder` by dispatching to the configured
embedding provider through LiteLLM, the same gateway the completion
providers use.

The ``(provider, model)`` pair is always explicit. Under the Explicit
Provider Binding rule a bare model name is never enough to dispatch, and
an embedder is no exception: the vectors in the store are only
comparable to each other if every one of them came from the same model,
so an ambiguous binding silently corrupts recall rather than failing.
"""

from typing import Final

from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_EMBEDDING_FAILED

logger = get_logger(__name__)

# LiteLLM routes on a "provider/model" identifier. Building it here keeps
# the joining rule in one place rather than at every call site.
_PROVIDER_MODEL_SEPARATOR: Final[str] = "/"


class ProviderTextEmbedder:
    """Embeds text through the configured embedding provider.

    Args:
        config: The resolved ``(provider, model, dims)`` binding.
    """

    def __init__(self, config: EmbedderConfig) -> None:
        self._config = config

    @property
    def dimensions(self) -> int:
        """Width of every vector this embedder produces."""
        return self._config.dims

    @property
    def model_ref(self) -> str:
        """The explicit provider-qualified model identifier."""
        return f"{self._config.provider}{_PROVIDER_MODEL_SEPARATOR}{self._config.model}"

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed a batch of texts, preserving input order.

        Batched rather than per-text because embedding endpoints are
        batch-oriented; one request per memory would multiply latency and
        cost on the retrieval path.

        Args:
            texts: Texts to embed.

        Returns:
            One vector per input text, in the same order.

        Raises:
            MemoryEmbeddingError: If the call fails, returns the wrong
                number of vectors, or returns a vector of unexpected
                width.
            MemoryError: Propagated; a system-level failure must not be
                reclassified as an embedding fault.
            RecursionError: Propagated, for the same reason.
        """
        if not texts:
            return ()
        from litellm import aembedding  # noqa: PLC0415 -- heavy import, call-time

        try:
            response = await aembedding(model=self.model_ref, input=list(texts))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MEMORY_EMBEDDING_FAILED,
                model=self.model_ref,
                batch_size=len(texts),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Embedding call failed for {self.model_ref!r}"
            raise MemoryEmbeddingError(msg) from exc
        return self._extract(response, expected=len(texts))

    def _extract(
        self,
        response: object,
        *,
        expected: int,
    ) -> tuple[tuple[float, ...], ...]:
        """Pull vectors out of the provider response and validate them.

        Args:
            response: The raw provider response.
            expected: How many vectors the batch should have produced.

        Returns:
            The vectors in input order.

        Raises:
            MemoryEmbeddingError: If the response shape, count or vector
                width is wrong. Each is treated as a hard failure rather
                than a partial result: a short or mis-sized batch would
                silently misalign vectors with the memories they belong
                to, which corrupts recall in a way no later stage can
                detect.
        """
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            msg = f"Embedding response from {self.model_ref!r} has no data list"
            raise MemoryEmbeddingError(msg)
        vectors: list[tuple[float, ...]] = []
        for item in data:
            raw = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(raw, list):
                msg = f"Embedding response from {self.model_ref!r} is malformed"
                raise MemoryEmbeddingError(msg)
            vector = tuple(float(value) for value in raw)
            if len(vector) != self._config.dims:
                msg = (
                    f"Embedder {self.model_ref!r} returned a {len(vector)}-dim "
                    f"vector but is configured for {self._config.dims}; the "
                    f"stored index would be incomparable"
                )
                raise MemoryEmbeddingError(msg)
            vectors.append(vector)
        if len(vectors) != expected:
            msg = (
                f"Embedder {self.model_ref!r} returned {len(vectors)} vectors "
                f"for {expected} inputs"
            )
            raise MemoryEmbeddingError(msg)
        return tuple(vectors)


__all__ = ["ProviderTextEmbedder"]
