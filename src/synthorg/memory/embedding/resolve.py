"""Embedder binding resolution.

The operator names the embedding model. This module reads that choice and
turns it into a usable binding; it does not make the choice, rank
candidates, infer a provider from a model name, or fall back to anything.

Resolution failing is deliberately an error rather than a silent default:
memory that cannot embed cannot retrieve by meaning, and degrading quietly
to keyword matching is how a dead memory layer stays unnoticed. The
built-in embedder is reachable only by naming it, never by failing into
it.

Priority, highest first:

1. Settings override (runtime-editable via the dashboard)
2. YAML config override (``CompanyMemoryConfig.embedder``)

The vector width is measured from the model rather than looked up, so a
model this codebase has never heard of is as usable as one it has.
"""

from collections.abc import Awaitable
from typing import Protocol

from synthorg.core.vector_limits import STORAGE_MAX_DIMENSIONS
from synthorg.memory.config import (
    CompanyMemoryConfig,
    EmbedderOverrideConfig,
)
from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.embedding.hashing import BUILTIN_EMBEDDER_DIMS
from synthorg.memory.embedding.probe import is_builtin_embedder, probe_embedder_dims
from synthorg.memory.errors import MemoryConfigError
from synthorg.observability import get_logger
from synthorg.observability.events.memory import MEMORY_EMBEDDER_BUILTIN_SELECTED

logger = get_logger(__name__)


class DimsProbe(Protocol):
    """Measures a model's true output width.

    Injected rather than imported at the call site so a test can resolve a
    binding without reaching a provider.
    """

    def __call__(self, *, provider: str, model: str) -> Awaitable[int]:
        """Return the width *model* emits."""
        ...


def _merge_override(
    override: EmbedderOverrideConfig | None,
    *,
    fallback_provider: str | None,
    fallback_model: str | None,
    fallback_dims: int | None,
) -> tuple[str | None, str | None, int | None]:
    """Merge an override with fallback values.

    Override fields take precedence; ``None`` falls through to
    fallback.

    Returns:
        Tuple ``(str | None, str | None, int | None)``.
    """
    if override is None:
        return fallback_provider, fallback_model, fallback_dims
    return (
        override.provider if override.provider is not None else fallback_provider,
        override.model if override.model is not None else fallback_model,
        override.dims if override.dims is not None else fallback_dims,
    )


def _dims_overridden(*overrides: EmbedderOverrideConfig | None) -> bool:
    """Whether any override in the chain set the vector width itself.

    Returns:
        ``True`` when an operator pinned ``dims`` rather than accepting the
        model's measured width.
    """
    return any(o is not None and o.dims is not None for o in overrides)


async def resolve_embedder_config(
    memory_config: CompanyMemoryConfig,
    *,
    settings_override: EmbedderOverrideConfig | None = None,
    measure_dims: DimsProbe = probe_embedder_dims,
) -> EmbedderConfig:
    """Resolve the operator's embedder choice into a usable binding.

    Args:
        memory_config: Company-wide memory configuration.
        settings_override: Runtime settings override (highest priority).
        measure_dims: Probe used to measure the model's width when the
            operator has not pinned one.

    Returns:
        A fully-populated ``EmbedderConfig``.

    Raises:
        MemoryConfigError: If no embedding model was chosen, if a model was
            chosen without a provider, or if the resolved width exceeds
            what the vector store can hold.
        MemoryEmbeddingError: If the chosen model could not be probed. It
            propagates rather than degrading: a model that cannot answer is
            not a model memory can be built on.
    """
    provider, model, dims = _merge_override(
        memory_config.embedder,
        fallback_provider=None,
        fallback_model=None,
        fallback_dims=None,
    )
    provider, model, dims = _merge_override(
        settings_override,
        fallback_provider=provider,
        fallback_model=model,
        fallback_dims=dims,
    )
    dims_explicit = _dims_overridden(memory_config.embedder, settings_override)

    provider, model = _chosen_or_refused(provider, model)
    builtin = is_builtin_embedder(provider, model)
    if builtin:
        logger.warning(
            MEMORY_EMBEDDER_BUILTIN_SELECTED,
            note=(
                "the built-in embedder matches shared vocabulary, not "
                "meaning; recall is materially weaker than with an "
                "embedding model"
            ),
        )
    if dims is None:
        # The built-in's width is definitional rather than discoverable:
        # it is a bucket count this process chooses, so there is nothing
        # to ask.
        dims = (
            BUILTIN_EMBEDDER_DIMS
            if builtin
            else await measure_dims(provider=provider, model=model)
        )
    _within_storage_ceiling(dims, model=model)

    return EmbedderConfig(
        provider=provider,
        model=model,
        dims=dims,
        dims_explicit=dims_explicit,
    )


def _chosen_or_refused(provider: str | None, model: str | None) -> tuple[str, str]:
    """Refuse a binding the operator never completed.

    Returns:
        The provider and model, both known present. Returned rather than
        merely checked so the guarantee survives the hand-off to
        ``EmbedderConfig``, which takes neither as optional.

    Raises:
        MemoryConfigError: If no model was chosen, or a model was chosen
            without the provider that serves it.
    """
    if model is None or not model.strip():
        msg = (
            "No embedding model is configured, so agents would start every "
            "task with no recall. Choose one in setup, or set "
            "memory.embedder_model."
        )
        raise MemoryConfigError(msg)
    if provider is None or not provider.strip():
        # Deriving the provider from the model name is what the Explicit
        # Provider Binding rule forbids, and it produced bindings that
        # named a provider no registry had.
        msg = (
            f"Embedding model {model!r} has no provider bound to it. Every "
            f"dispatch resolves an explicit (provider, model) pair; set "
            f'memory.embedder_model to {{"provider": ..., "model_id": '
            f"{model!r}}}."
        )
        raise MemoryConfigError(msg)
    return provider, model


def _within_storage_ceiling(dims: int, *, model: str) -> None:
    """Refuse a width no vector column could hold.

    A width above the store's index ceiling is not refused here: those
    vectors are stored and searched correctly, just without an approximate
    index, which the memory health surface reports as degraded. Only a
    width the store cannot hold at all is fatal.

    Raises:
        MemoryConfigError: If the width exceeds the storage ceiling.
    """
    if dims <= STORAGE_MAX_DIMENSIONS:
        return
    msg = (
        f"Embedding model {model!r} emits {dims} dimensions, above the "
        f"vector store's ceiling of {STORAGE_MAX_DIMENSIONS}; choose a "
        f"narrower model, or set memory.embedder_dims to truncate it."
    )
    raise MemoryConfigError(msg)


__all__ = ["DimsProbe", "resolve_embedder_config"]
