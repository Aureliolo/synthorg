"""Factory for creating memory backends from configuration.

Each company gets its own ``MemoryBackend`` instance.  The factory
dispatches to concrete backend implementations based on
``config.backend``.
"""

import builtins
from typing import TYPE_CHECKING

from synthorg.memory.config import CompanyMemoryConfig  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.memory.backends.mem0.config import Mem0EmbedderConfig
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CONFIG_INVALID,
    MEMORY_BACKEND_CREATED,
    MEMORY_BACKEND_SYSTEM_ERROR,
    MEMORY_BACKEND_UNKNOWN,
)

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
    """
    from synthorg.memory.backends.mem0 import Mem0MemoryBackend  # noqa: PLC0415
    from synthorg.memory.backends.mem0.config import (  # noqa: PLC0415
        Mem0EmbedderConfig,
        build_config_from_company_config,
    )

    if embedder is None:
        msg = (
            "Mem0 backend requires an embedder configuration — "
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
        logger.exception(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="create_mem0_backend",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        msg = f"Invalid Mem0 configuration: {exc}"
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
        logger.exception(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="create_mem0_backend",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        msg = f"Failed to create Mem0 backend: {exc}"
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
    if config.backend == "mem0":
        return _create_mem0_backend(config, embedder=embedder)
    # Defensive guard: config validation rejects unknown backends, so
    # this branch is unreachable under normal construction.  It exists
    # as a safety net for callers that bypass validation (e.g. via
    # model_construct).
    msg = f"Unknown memory backend: {config.backend!r}"
    logger.error(MEMORY_BACKEND_UNKNOWN, backend=config.backend)
    raise MemoryConfigError(msg)
