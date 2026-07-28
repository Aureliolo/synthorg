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

from typing import Final

from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_DIMS,
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
)
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_EMBEDDER_PROBED

logger = get_logger(__name__)

#: Short, content-free, and stable. Content-free because the vector is
#: discarded and only its length is read; stable so a provider-side cache
#: makes a repeat probe free.
_PROBE_TEXT: Final[str] = "embedding width probe"

_PROVIDER_MODEL_SEPARATOR: Final[str] = "/"


def is_builtin_embedder(provider: str, model: str) -> bool:
    """Whether a binding names the built-in embedder.

    Returns:
        ``True`` when both halves match the built-in binding.
    """
    return provider == BUILTIN_EMBEDDER_PROVIDER and model == BUILTIN_EMBEDDER_MODEL


async def probe_embedder_dims(*, provider: str, model: str) -> int:
    """Return the vector width *model* actually emits.

    Args:
        provider: Embedding provider name.
        model: Embedding model identifier.

    Returns:
        The measured width, which for the built-in embedder is known
        without a call.

    Raises:
        MemoryEmbeddingError: If the model cannot be reached, refuses the
            request, or answers with a shape that carries no vector. The
            caller must surface that: a binding whose width cannot be
            measured is not a binding memory can be built on, and
            substituting a guess or another embedder would hide it.
        MemoryError: Propagated; a system-level failure must not be
            reclassified as a probe fault.
        RecursionError: Propagated, for the same reason.
    """
    if is_builtin_embedder(provider, model):
        return BUILTIN_EMBEDDER_DIMS

    model_ref = f"{provider}{_PROVIDER_MODEL_SEPARATOR}{model}"
    from litellm import aembedding  # noqa: PLC0415 -- heavy import, call-time

    try:
        response = await aembedding(model=model_ref, input=[_PROBE_TEXT])
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            MEMORY_EMBEDDER_PROBED,
            model=model_ref,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Could not measure the embedding width of {model_ref!r}: the "
            f"model did not answer a probe request"
        )
        raise MemoryEmbeddingError(msg) from exc

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
        msg = (
            f"Embedding probe of {model_ref!r} returned no vector, so its "
            f"width is unknown"
        )
        raise MemoryEmbeddingError(msg)
    return len(raw)


__all__ = ["is_builtin_embedder", "probe_embedder_dims"]
