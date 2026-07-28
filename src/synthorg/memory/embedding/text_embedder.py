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
from datetime import UTC, datetime
from typing import Final

from litellm.exceptions import (
    APIConnectionError as LiteLLMConnectionError,
)
from litellm.exceptions import (
    InternalServerError as LiteLLMInternalError,
)
from litellm.exceptions import (
    RateLimitError as LiteLLMRateLimit,
)
from litellm.exceptions import (
    ServiceUnavailableError as LiteLLMUnavailable,
)
from litellm.exceptions import (
    Timeout as LiteLLMTimeout,
)

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDING_COST_RECORD_FAILED,
    MEMORY_EMBEDDING_FAILED,
    MEMORY_EMBEDDING_RETRIED,
    MEMORY_EMBEDDING_TRUNCATED,
)
from synthorg.providers.cost_recording import resolve_currency

logger = get_logger(__name__)

# LiteLLM routes on a "provider/model" identifier. Building it here keeps
# the joining rule in one place rather than at every call site.
_PROVIDER_MODEL_SEPARATOR: Final[str] = "/"

# Matches the provider-layer defaults: an embedding endpoint fails the
# same way a completion endpoint does (429, 5xx, connection reset), so
# there is no case for a different budget here.
_RETRY_MAX_ATTEMPTS: Final[int] = 3
_RETRY_BASE_SECONDS: Final[float] = 0.5
_RETRY_CAP_SECONDS: Final[float] = 8.0

# Cost attribution needs an owner. Embedding is issued by the memory
# subsystem on behalf of the whole company rather than by any one agent
# or task, so it is attributed to the subsystem instead of being charged
# to whichever agent happened to trigger the recall.
_SYSTEM_AGENT_ID: Final[NotBlankStr] = NotBlankStr("system:memory")
_SYSTEM_TASK_ID: Final[NotBlankStr] = NotBlankStr("system:memory:embedding")


# LiteLLM's own transient exception types. A deterministic fault
# (auth, bad request, model-not-found, content policy) is NOT here: it
# repeats identically, so retrying it only burns the backoff budget on
# the read + write hot path and masks the real cause behind a generic
# retry-exhausted error. Mirrors the completion driver's own mapping.
_RETRYABLE_EMBEDDING_ERRORS: Final[tuple[type[Exception], ...]] = (
    LiteLLMRateLimit,
    LiteLLMTimeout,
    LiteLLMUnavailable,
    LiteLLMInternalError,
    LiteLLMConnectionError,
)


def _is_retryable(exc: Exception) -> bool:
    """Whether an embedding failure is worth another attempt.

    Only genuinely transient provider faults (rate limit, timeout, 5xx,
    connection reset) retry; a deterministic misconfiguration surfaces
    immediately instead of repeating three times.

    Returns:
        ``True`` when the call should be retried.
    """
    return isinstance(exc, _RETRYABLE_EMBEDDING_ERRORS)


class ProviderTextEmbedder:
    """Embeds text through the configured embedding provider.

    Args:
        config: The resolved ``(provider, model, dims)`` binding.
        cost_tracker: Sink for per-batch spend. ``None`` leaves the call
            unmetered, which is correct only where no tracker exists
            (tests, the trackerless eval harness).
    """

    def __init__(
        self,
        config: EmbedderConfig,
        *,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._config = config
        self._cost_tracker = cost_tracker
        self._retry = GeneralRetryHandler(
            retryable=_is_retryable,
            max_attempts=_RETRY_MAX_ATTEMPTS,
            base=_RETRY_BASE_SECONDS,
            cap=_RETRY_CAP_SECONDS,
            event=MEMORY_EMBEDDING_RETRIED,
        )

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
            response = await self._retry.execute(
                lambda: aembedding(model=self.model_ref, input=list(texts))
            )
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
        await self._record_cost(response)
        return self._extract(response, expected=len(texts))

    async def _record_cost(self, response: object) -> None:
        """Attribute one embedding batch's spend to the memory subsystem.

        Best-effort: losing a cost record is not worth losing the
        embedding, so a tracker failure is reported and the call
        continues.
        """
        if self._cost_tracker is None:
            return
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        cost = getattr(response, "_hidden_params", {}).get("response_cost") or 0.0
        try:
            await self._cost_tracker.record(
                CostRecord(
                    agent_id=_SYSTEM_AGENT_ID,
                    task_id=_SYSTEM_TASK_ID,
                    provider=NotBlankStr(self._config.provider),
                    model=NotBlankStr(self._config.model),
                    input_tokens=int(prompt_tokens),
                    output_tokens=0,
                    cost=float(cost),
                    currency=resolve_currency(self._cost_tracker),
                    timestamp=datetime.now(UTC),
                    call_category=LLMCallCategory.EMBEDDING,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- accounting side channel
            reraise_critical(exc)
            logger.warning(
                MEMORY_EMBEDDING_COST_RECORD_FAILED,
                model=self.model_ref,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

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
