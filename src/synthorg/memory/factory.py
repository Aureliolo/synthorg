"""Factory for creating memory backends from configuration.

Each company gets its own ``MemoryBackend`` instance. The factory
dispatches on ``config.backend`` via :class:`MemoryBackendRegistry`.

Unlike a pure config factory, this one also takes the collaborators the
durable backend cannot invent for itself: a
:class:`MemoryVectorRepository` from the persistence layer and a
:class:`TextEmbedder`. Boot wiring resolves both and passes them in, so
the backend never reaches across the persistence boundary itself.
"""

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.memory.backend_deps import MemoryBackendDeps
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.registry import MemoryBackendRegistry
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CREATED,
    MEMORY_BACKEND_UNKNOWN,
)
from synthorg.observability.redaction import safe_error_description

logger = get_logger(__name__)

SQL_VECTOR_BACKEND = "sqlvector"
IN_MEMORY_BACKEND = "inmemory"
COMPOSITE_BACKEND = "composite"


def _create_sqlvector_backend(
    config: CompanyMemoryConfig,
    *,
    deps: MemoryBackendDeps,
) -> MemoryBackend:
    """Create the durable SQL-backed memory backend.

    Args:
        config: Company-wide memory configuration.
        deps: Injected collaborators; ``repository`` is required.

    Returns:
        A new, disconnected ``SqlVectorBackend``.

    Raises:
        MemoryConfigError: If no repository was supplied.
    """
    from synthorg.memory.backends.sqlvector import SqlVectorBackend  # noqa: PLC0415

    if deps.repository is None:
        msg = (
            "The 'sqlvector' backend requires a MemoryVectorRepository; "
            "boot wiring must resolve one from the persistence layer"
        )
        raise MemoryConfigError(msg)
    backend = SqlVectorBackend(
        deps.repository,
        embedder=deps.embedder,
        max_memories_per_agent=config.options.max_memories_per_agent,
        clock=deps.clock,
    )
    logger.info(
        MEMORY_BACKEND_CREATED,
        backend=SQL_VECTOR_BACKEND,
        dense_search=deps.embedder is not None,
    )
    return backend


def _create_inmemory_entry(
    config: CompanyMemoryConfig,
    *,
    deps: MemoryBackendDeps,  # noqa: ARG001 -- registry signature parity; this backend needs none
) -> MemoryBackend:
    """Registry entry for the ephemeral backend.

    Returns:
        A new, disconnected ``InMemoryBackend``.
    """
    return _create_inmemory_backend(config)


def _create_inmemory_backend(config: CompanyMemoryConfig) -> MemoryBackend:
    """Create the ephemeral, keyword-only backend.

    Retained as an explicit operator opt-in, never an automatic
    fallback: it loses every memory on restart and matches by substring
    rather than meaning, so silently selecting it would look like
    working memory while quietly recalling the wrong things.

    Args:
        config: Company-wide memory configuration.

    Returns:
        A new, disconnected ``InMemoryBackend``.
    """
    from synthorg.memory.backends.inmemory import InMemoryBackend  # noqa: PLC0415

    backend = InMemoryBackend(
        max_memories_per_agent=config.options.max_memories_per_agent,
    )
    logger.warning(
        MEMORY_BACKEND_CREATED,
        backend=IN_MEMORY_BACKEND,
        durable=False,
        note="ephemeral keyword-only memory; recall is lost on restart",
    )
    return backend


def _create_composite_backend(
    config: CompanyMemoryConfig,
    *,
    deps: MemoryBackendDeps,
) -> MemoryBackend:
    """Create a namespace-routing backend over leaf backends.

    Args:
        config: Company-wide memory configuration with ``composite`` set.
        deps: Injected collaborators, passed through to each child.

    Returns:
        A new, disconnected ``CompositeBackend``.

    Raises:
        MemoryConfigError: If the composite config is missing or a child
            names an unknown backend.
    """
    from synthorg.memory.backends.composite import CompositeBackend  # noqa: PLC0415

    if config.composite is None:  # pragma: no cover -- guarded by validator
        msg = "composite config is required when backend is 'composite'"
        raise MemoryConfigError(msg)
    composite_cfg = config.composite
    names: set[str] = set(composite_cfg.routes.values())
    names.add(composite_cfg.default)
    children: dict[str, MemoryBackend] = {}
    for name in sorted(names):
        try:
            children[name] = _LEAF_REGISTRY.build(name, config, deps=deps)
        except StrategyFactoryNotFoundError as exc:
            msg = f"Composite child {name!r} is not a recognised backend"
            logger.warning(
                MEMORY_BACKEND_UNKNOWN,
                backend=name,
                error=msg,
                error_type=type(exc).__name__,
            )
            raise MemoryConfigError(msg) from exc
    backend = CompositeBackend(children=children, config=composite_cfg)
    logger.info(
        MEMORY_BACKEND_CREATED,
        backend=COMPOSITE_BACKEND,
        children=sorted(children),
    )
    return backend


# A composite child must itself be non-composite, keeping the wiring
# acyclic by construction rather than by a runtime depth check.
_LEAF_REGISTRY: MemoryBackendRegistry = MemoryBackendRegistry(
    {
        SQL_VECTOR_BACKEND: _create_sqlvector_backend,
        IN_MEMORY_BACKEND: _create_inmemory_entry,
    },
)

_REGISTRY: MemoryBackendRegistry = MemoryBackendRegistry(
    {
        SQL_VECTOR_BACKEND: _create_sqlvector_backend,
        IN_MEMORY_BACKEND: _create_inmemory_entry,
        COMPOSITE_BACKEND: _create_composite_backend,
    },
)


def default_registry() -> MemoryBackendRegistry:
    """Return the module-level registry of built-in backends.

    Returns:
        The registry.
    """
    return _REGISTRY


def build_in_memory_backend() -> MemoryBackend:
    """Build the ephemeral backend directly, bypassing configuration.

    Exists for tests and for the explicitly-degraded operator path. It is
    never the automatic answer to a missing embedder: that case fails
    loud instead, because a silent fallback to keyword-only memory is
    exactly how a dead memory layer went unnoticed before.

    Returns:
        A new, disconnected ``InMemoryBackend``.
    """
    return _create_inmemory_backend(CompanyMemoryConfig())


def create_memory_backend(
    config: CompanyMemoryConfig,
    *,
    deps: MemoryBackendDeps | None = None,
) -> MemoryBackend:
    """Create the memory backend named by ``config.backend``.

    Args:
        config: Memory configuration, including backend selection.
        deps: Collaborators the chosen backend needs.

    Returns:
        A new, disconnected backend. The caller must ``connect()``.

    Raises:
        MemoryConfigError: If the backend name is unknown or the chosen
            backend is missing a required collaborator.
    """
    try:
        return _REGISTRY.build(config.backend, config, deps=deps or MemoryBackendDeps())
    except StrategyFactoryNotFoundError as exc:
        msg = f"Unknown memory backend: {config.backend!r}"
        logger.warning(
            MEMORY_BACKEND_UNKNOWN,
            backend=config.backend,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MemoryConfigError(msg) from exc


__all__ = [
    "COMPOSITE_BACKEND",
    "IN_MEMORY_BACKEND",
    "SQL_VECTOR_BACKEND",
    "MemoryBackendDeps",
    "build_in_memory_backend",
    "create_memory_backend",
    "default_registry",
]
