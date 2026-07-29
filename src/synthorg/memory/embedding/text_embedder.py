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

Two properties this call needs that a bare gateway call does not give
it. It sits on the read path of every recall and the write path of every
memory, so a single transient rate limit would otherwise fail the whole
operation: hence bounded retry. And it spends real money on a provider
quota shared with completion traffic, so every batch is attributed as
:attr:`LLMCallCategory.EMBEDDING` rather than becoming unattributed
spend an operator cannot see.
"""

import math

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.embedding.dispatch import (
    DEFAULT_EMBED_TIMEOUT_SECONDS,
    embedding_retry_handler,
    format_model_ref,
    record_embedding_cost,
    with_deadline,
)
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDING_FAILED,
    MEMORY_EMBEDDING_TRUNCATED,
)

logger = get_logger(__name__)


class ProviderTextEmbedder:
    """Embeds text through the configured embedding provider.

    Args:
        config: The resolved ``(provider, model, dims)`` binding.
        cost_tracker: Sink for per-batch spend. ``None`` leaves the call
            unmetered, which is correct only where no tracker exists
            (tests, the trackerless eval harness).
        timeout_seconds: Wall-clock ceiling for one batch, retries
            included.
    """

    def __init__(
        self,
        config: EmbedderConfig,
        *,
        cost_tracker: CostTrackerProtocol | None = None,
        timeout_seconds: float = DEFAULT_EMBED_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._cost_tracker = cost_tracker
        self._timeout_seconds = timeout_seconds
        self._retry = embedding_retry_handler()

    @property
    def dimensions(self) -> int:
        """Width of every vector this embedder produces."""
        return self._config.dims

    @property
    def model_ref(self) -> str:
        """The explicit provider-qualified model identifier."""
        return format_model_ref(self._config.provider, self._config.model)

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
            MemoryEmbeddingError: If the call fails, exceeds the deadline,
                returns the wrong number of vectors, or returns a vector
                of unexpected width.
            MemoryError: Propagated; a system-level failure must not be
                reclassified as an embedding fault.
            RecursionError: Propagated, for the same reason.
        """
        if not texts:
            return ()
        from litellm import aembedding  # noqa: PLC0415 -- heavy import, call-time

        try:
            response = await with_deadline(
                lambda: self._retry.execute(
                    lambda: aembedding(model=self.model_ref, input=list(texts))
                ),
                timeout_seconds=self._timeout_seconds,
            )
        except MemoryError, RecursionError:
            raise
        except TimeoutError as exc:
            logger.warning(
                MEMORY_EMBEDDING_FAILED,
                model=self.model_ref,
                batch_size=len(texts),
                timeout_seconds=self._timeout_seconds,
                reason="deadline_exceeded",
            )
            msg = (
                f"Embedding call for {self.model_ref!r} did not answer "
                f"within {self._timeout_seconds:g}s"
            )
            raise MemoryEmbeddingError(msg) from exc
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
        await record_embedding_cost(
            response,
            cost_tracker=self._cost_tracker,
            provider=self._config.provider,
            model=self._config.model,
        )
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
        # Bind every vector to its declared ``index`` rather than trusting
        # list order: providers (and LiteLLM's cache-merge path) can return
        # items reordered or duplicated, and a silently-misaligned batch
        # would store each memory against another's vector, corrupting
        # recall in a way no later stage can detect.
        slots: list[tuple[float, ...] | None] = [None] * expected
        for item in data:
            raw = item.get("embedding") if isinstance(item, dict) else None
            index = item.get("index") if isinstance(item, dict) else None
            if not isinstance(raw, list) or not isinstance(index, int):
                msg = f"Embedding response from {self.model_ref!r} is malformed"
                raise MemoryEmbeddingError(msg)
            if not 0 <= index < expected:
                msg = (
                    f"Embedder {self.model_ref!r} returned index {index} outside "
                    f"the expected range for {expected} inputs"
                )
                raise MemoryEmbeddingError(msg)
            if slots[index] is not None:
                msg = (
                    f"Embedder {self.model_ref!r} returned a duplicate vector "
                    f"for index {index}"
                )
                raise MemoryEmbeddingError(msg)
            try:
                vector = tuple(float(value) for value in raw)
            except (TypeError, ValueError, OverflowError) as exc:
                msg = f"Embedding response from {self.model_ref!r} is malformed"
                raise MemoryEmbeddingError(msg) from exc
            # A NaN or infinity survives ``float()`` without raising, yet an
            # embedding carrying one is unusable: distance maths against it is
            # meaningless and the store forbids non-finite components.
            if not all(math.isfinite(component) for component in vector):
                msg = f"Embedding response from {self.model_ref!r} is malformed"
                raise MemoryEmbeddingError(msg)
            slots[index] = self._fit_width(vector)
        vectors: list[tuple[float, ...]] = []
        for position, filled in enumerate(slots):
            if filled is None:
                msg = (
                    f"Embedder {self.model_ref!r} returned no vector for input "
                    f"{position} of {expected}"
                )
                raise MemoryEmbeddingError(msg)
            vectors.append(filled)
        return tuple(vectors)

    def _fit_width(self, vector: tuple[float, ...]) -> tuple[float, ...]:
        """Return *vector* at the configured width.

        A model that emits more components than the operator asked for is
        being used through its Matryoshka representation: the leading
        components are trained to stand alone, so the tail is dropped and the
        remainder renormalised to keep distances comparable with vectors the
        same embedder produced. It is the mechanism this embedder offers for
        bringing a wide model under a vector store's index ceiling.

        Returns:
            The vector, truncated and renormalised when the operator pinned a
            narrower width.

        Raises:
            MemoryEmbeddingError: If the width differs for any other reason.
                Padding a short vector, or silently accepting a long one the
                operator did not ask to narrow, would leave the stored index
                incomparable.
        """
        width = self._config.dims
        if len(vector) == width:
            return vector
        if not (self._config.dims_explicit and len(vector) > width):
            msg = (
                f"Embedder {self.model_ref!r} returned a {len(vector)}-dim "
                f"vector but is configured for {width}; the stored index "
                f"would be incomparable"
            )
            logger.warning(
                MEMORY_EMBEDDING_FAILED,
                model=self.model_ref,
                native_dims=len(vector),
                target_dims=width,
                dims_explicit=self._config.dims_explicit,
                reason="embedder width does not match configuration",
            )
            raise MemoryEmbeddingError(msg)
        logger.debug(
            MEMORY_EMBEDDING_TRUNCATED,
            model=self.model_ref,
            native_dims=len(vector),
            target_dims=width,
        )
        head = vector[:width]
        norm = math.sqrt(sum(component * component for component in head))
        if norm == 0.0:
            # An all-zero head carries no direction; renormalising would
            # divide by zero and the vector is already at the target width.
            return head
        return tuple(component / norm for component in head)


__all__ = ["ProviderTextEmbedder"]
