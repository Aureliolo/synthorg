"""Factory for creating memory backends from configuration.

Each company gets its own ``MemoryBackend`` instance.  The factory
dispatches to concrete backend implementations based on
``config.backend`` via :class:`MemoryBackendRegistry`.
"""

import builtins

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.memory.backends.mem0.config import (
    Mem0EmbedderConfig,
)
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.registry import MemoryBackendRegistry
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CONFIG_INVALID,
    MEMORY_BACKEND_CREATED,
    MEMORY_BACKEND_SYSTEM_ERROR,
    MEMORY_BACKEND_UNKNOWN,
)
from synthorg.observability.redaction import safe_error_description

logger = get_logger(__name__)


def _create_mem0_backend(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig | None,
) -> MemoryBackend:
    """Create a Mem0 memory backend from configuration.

    Args:
        config: Company-wide memory configuration.
        embedder: Mem0-specific embedder configuration (required).

    Returns:
        A new, disconnected ``Mem0MemoryBackend`` instance.

    Raises:
        MemoryConfigError: If embedder is missing/invalid or
            backend construction fails.
        MemoryError: If the related operation fails.
        RecursionError: If the related operation fails.
    """
    from synthorg.memory.backends.mem0 import Mem0MemoryBackend  # noqa: PLC0415
    from synthorg.memory.backends.mem0.config import (  # noqa: PLC0415
        build_config_from_company_config,
    )

    if embedder is None:
        msg = (
            "Mem0 backend requires an embedder configuration -- "
            "pass a Mem0EmbedderConfig instance"
        )
        logger.warning(
            MEMORY_BACKEND_CONFIG_INVALID,
            backend="mem0",
            reason="missing_embedder",
            error=msg,
        )
        raise MemoryConfigError(msg)
    if not isinstance(embedder, Mem0EmbedderConfig):
        msg = (  # type: ignore[unreachable]
            f"embedder must be a Mem0EmbedderConfig, got {type(embedder).__name__}"
        )
        logger.warning(
            MEMORY_BACKEND_CONFIG_INVALID,
            backend="mem0",
            reason="invalid_embedder_type",
            error=msg,
            embedder_type=type(embedder).__name__,
        )
        raise MemoryConfigError(msg)

    try:
        mem0_config = build_config_from_company_config(
            config,
            embedder=embedder,
        )
    except (builtins.MemoryError, RecursionError) as exc:
        logger.warning(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="create_mem0_backend",
            error=safe_error_description(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        reraise_critical(exc)
        msg = f"Invalid Mem0 configuration: {safe_error_description(exc)}"
        logger.warning(
            MEMORY_BACKEND_CONFIG_INVALID,
            backend="mem0",
            reason="config_build_failed",
            error=msg,
            error_type=type(exc).__name__,
        )
        raise MemoryConfigError(msg) from exc
    try:
        backend = Mem0MemoryBackend(
            mem0_config=mem0_config,
            max_memories_per_agent=config.options.max_memories_per_agent,
        )
    except (builtins.MemoryError, RecursionError) as exc:
        logger.warning(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="create_mem0_backend",
            error=safe_error_description(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        reraise_critical(exc)
        msg = f"Failed to create Mem0 backend: {safe_error_description(exc)}"
        logger.warning(
            MEMORY_BACKEND_CONFIG_INVALID,
            backend="mem0",
            reason="backend_init_failed",
            error=msg,
            error_type=type(exc).__name__,
        )
        raise MemoryConfigError(msg) from exc
    logger.info(
        MEMORY_BACKEND_CREATED,
        backend="mem0",
        data_dir=mem0_config.data_dir,
    )
    return backend


def _create_inmemory_backend(
    config: CompanyMemoryConfig,
) -> MemoryBackend:
    """Create an in-memory (session-scoped) backend.

    Args:
        config: Company-wide memory configuration.

    Returns:
        A new, disconnected ``InMemoryBackend`` instance.
    """
    from synthorg.memory.backends.inmemory import (  # noqa: PLC0415
        InMemoryBackend,
    )

    backend = InMemoryBackend(
        max_memories_per_agent=config.options.max_memories_per_agent,
    )
    logger.info(MEMORY_BACKEND_CREATED, backend="inmemory")
    return backend


def _create_composite_backend(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig | None,
) -> MemoryBackend:
    """Create a composite backend with namespace routing.

    Args:
        config: Company-wide memory configuration (must have
            ``composite`` set).
        embedder: Embedder config, passed through to child mem0
            backends.

    Returns:
        A new, disconnected ``CompositeBackend`` instance.

    Raises:
        MemoryConfigError: If composite config is missing or
            child backends cannot be created.
    """
    from synthorg.memory.backends.composite import (  # noqa: PLC0415
        CompositeBackend,
    )

    if config.composite is None:  # pragma: no cover -- guarded by validator
        msg = "composite config is required when backend is 'composite'"
        raise MemoryConfigError(msg)
    composite_cfg = config.composite
    # Collect unique backend names from routes + default.
    names: set[str] = set(composite_cfg.routes.values())
    names.add(composite_cfg.default)
    # Create each leaf backend once via the leaf registry (composite
    # children may not themselves be composite).
    children: dict[str, MemoryBackend] = {}
    for name in sorted(names):
        try:
            children[name] = _LEAF_REGISTRY.build(name, config, embedder=embedder)
        except StrategyFactoryNotFoundError as exc:
            msg = f"Composite child '{name}' is not a recognized backend"
            logger.warning(
                MEMORY_BACKEND_UNKNOWN,
                backend=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise MemoryConfigError(msg) from exc
    backend = CompositeBackend(
        children=children,
        config=composite_cfg,
    )
    logger.info(
        MEMORY_BACKEND_CREATED,
        backend="composite",
        children=sorted(children.keys()),
    )
    return backend


def _build_mem0_entry(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig | None,
) -> MemoryBackend:
    """Registry entry: build the mem0 backend (requires an embedder).

    Returns:
        A disconnected ``Mem0MemoryBackend``.
    """
    return _create_mem0_backend(config, embedder=embedder)


def _build_inmemory_entry(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig | None,  # noqa: ARG001
) -> MemoryBackend:
    """Registry entry: build the in-memory backend (embedder ignored).

    Returns:
        A disconnected ``InMemoryBackend``.
    """
    return _create_inmemory_backend(config)


def _build_composite_entry(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig | None,
) -> MemoryBackend:
    """Registry entry: build the namespace-routing composite backend.

    Returns:
        A disconnected ``CompositeBackend`` wrapping the configured children.
    """
    return _create_composite_backend(config, embedder=embedder)


# Leaf registry (mem0, inmemory) used by the composite child loop -- a
# composite child must itself be a non-composite backend to keep the
# wiring acyclic.
_LEAF_REGISTRY: MemoryBackendRegistry = MemoryBackendRegistry(
    {
        "mem0": _build_mem0_entry,
        "inmemory": _build_inmemory_entry,
    },
)

# Top-level registry used by ``create_memory_backend``.
_REGISTRY: MemoryBackendRegistry = MemoryBackendRegistry(
    {
        "mem0": _build_mem0_entry,
        "inmemory": _build_inmemory_entry,
        "composite": _build_composite_entry,
    },
)


def default_registry() -> MemoryBackendRegistry:
    """Return the module-level registry containing the built-in backends.

    Returns:
        Result of type ``MemoryBackendRegistry``.
    """
    return _REGISTRY


def create_memory_backend(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig | None = None,
) -> MemoryBackend:
    """Create a memory backend from configuration.

    Args:
        config: Memory configuration (includes backend selection and
            backend-specific settings).
        embedder: Backend-specific embedder configuration.  Required
            for the ``"mem0"`` backend (must be a
            ``Mem0EmbedderConfig`` instance).

    Returns:
        A new, disconnected backend instance.  The caller must call
        ``connect()`` before use.

    Raises:
        MemoryConfigError: If the backend is not recognized or
            required configuration is missing.
    """
    try:
        return _REGISTRY.build(config.backend, config, embedder=embedder)
    except StrategyFactoryNotFoundError as exc:
        msg = f"Unknown memory backend: {config.backend!r}"
        logger.warning(
            MEMORY_BACKEND_UNKNOWN,
            backend=config.backend,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MemoryConfigError(msg) from exc
