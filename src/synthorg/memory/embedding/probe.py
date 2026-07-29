# module-kind: adapter
"""Measure an embedding model's true output width by asking it.

The width the vector column is built for has to be the width the model
actually emits, and the only authority on that is the model. A shipped
table of catalogued widths cannot be that authority: it goes stale, it
cannot cover a model it has never heard of, and being wrong by even one
component makes every stored vector incomparable.

So the width is measured, once, by embedding a short probe string and
counting components. That call doubles as proof the binding works at all:
a model that cannot embed fails here, at selection, rather than at the
first memory write.
"""

import builtins
from typing import Final

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.memory.embedding.dispatch import (
    embedding_retry_handler,
    format_model_ref,
    record_embedding_cost,
    with_deadline,
)
from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_DIMS,
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
)
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDER_PROBE_FAILED,
    MEMORY_EMBEDDER_PROBED,
)

logger = get_logger(__name__)

#: Short, content-free, and stable. Content-free because the vector is
#: discarded and only its length is read; stable so a provider-side cache
#: makes a repeat probe free.
_PROBE_TEXT: Final[str] = "embedding width probe"

#: Wall-clock ceiling for one probe, retries included. This call sits on
#: the boot path and inside setup completion's process-wide lock, so an
#: endpoint that accepts the connection and never answers would otherwise
#: hang startup outright and stall every concurrent setup attempt.
DEFAULT_PROBE_TIMEOUT_SECONDS: Final[float] = 30.0


def is_builtin_embedder(provider: str, model: str) -> bool:
    """Whether a binding names the built-in embedder.

    Returns:
        ``True`` when both halves match the built-in binding.
    """
    return provider == BUILTIN_EMBEDDER_PROVIDER and model == BUILTIN_EMBEDDER_MODEL


async def probe_embedder_dims(
    *,
    provider: str,
    model: str,
    cost_tracker: CostTrackerProtocol | None = None,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> int:
    """Return the vector width *model* actually emits.

    Retries only genuinely transient provider faults, on the same budget
    the serving embedder uses. A model that is simply wrong fails on the
    first attempt; a connection that dropped once does not cost the
    operator their memory subsystem for the rest of the process.

    Args:
        provider: Embedding provider name.
        model: Embedding model identifier.
        cost_tracker: Sink for the probe's own spend. The probe is a real
            billable call against the same quota as retrieval traffic, so
            it is attributed rather than issued invisibly.
        timeout_seconds: Wall-clock ceiling for the whole attempt.

    Returns:
        The measured width, which for the built-in embedder is known
        without a call.

    Raises:
        MemoryEmbeddingError: If the model cannot be reached, refuses the
            request, exceeds the deadline, or answers with a shape that
            carries no vector. The caller must surface that: a binding
            whose width cannot be measured is not a binding memory can be
            built on, and substituting a guess or another embedder would
            hide it.
        MemoryError: Propagated; a system-level failure must not be
            reclassified as a probe fault.
        RecursionError: Propagated, for the same reason.
    """
    if is_builtin_embedder(provider, model):
        return BUILTIN_EMBEDDER_DIMS

    model_ref = format_model_ref(provider, model)
    from litellm import aembedding  # noqa: PLC0415 -- heavy import, call-time

    retry = embedding_retry_handler()
    try:
        response = await with_deadline(
            lambda: retry.execute(
                lambda: aembedding(model=model_ref, input=[_PROBE_TEXT])
            ),
            timeout_seconds=timeout_seconds,
        )
    except builtins.MemoryError, RecursionError:
        raise
    except TimeoutError as exc:
        logger.warning(
            MEMORY_EMBEDDER_PROBE_FAILED,
            model=model_ref,
            timeout_seconds=timeout_seconds,
            reason="deadline_exceeded",
        )
        msg = (
            f"Could not measure the embedding width of {model_ref!r}: the "
            f"model did not answer within {timeout_seconds:g}s"
        )
        raise MemoryEmbeddingError(msg) from exc
    except Exception as exc:
        logger.warning(
            MEMORY_EMBEDDER_PROBE_FAILED,
            model=model_ref,
            reason="call_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Could not measure the embedding width of {model_ref!r}: the "
            f"model did not answer a probe request"
        )
        raise MemoryEmbeddingError(msg) from exc

    await record_embedding_cost(
        response,
        cost_tracker=cost_tracker,
        provider=provider,
        model=model,
    )
    width = _width_of(response, model_ref=model_ref)
    logger.info(MEMORY_EMBEDDER_PROBED, model=model_ref, dims=width)
    return width


def _width_of(response: object, *, model_ref: str) -> int:
    """Read the vector width out of a probe response.

    Returns:
        The number of components in the probe vector.

    Raises:
        MemoryEmbeddingError: If the response carries no usable vector, or
            carries an empty one.
    """
    data = getattr(response, "data", None)
    first = data[0] if isinstance(data, list) and data else None
    raw = first.get("embedding") if isinstance(first, dict) else None
    if not isinstance(raw, list) or not raw:
        # The sibling failure branch logs before raising; this one is the
        # same class of fault (the model did not answer usefully) and needs
        # the same trail, or an operator sees the raised error with nothing
        # in the log naming which model produced it.
        logger.warning(
            MEMORY_EMBEDDER_PROBE_FAILED,
            model=model_ref,
            reason="response_carried_no_vector",
        )
        msg = (
            f"Embedding probe of {model_ref!r} returned no vector, so its "
            f"width is unknown"
        )
        raise MemoryEmbeddingError(msg)
    return len(raw)


__all__ = ["is_builtin_embedder", "probe_embedder_dims"]
