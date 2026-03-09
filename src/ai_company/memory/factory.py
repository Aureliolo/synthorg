"""Factory for creating memory backends from configuration.

Each company gets its own ``MemoryBackend`` instance.  Concrete
backend registration happens in issue #41 (Mem0 adapter).
"""

from ai_company.memory.config import CompanyMemoryConfig  # noqa: TC001
from ai_company.memory.errors import MemoryConfigError
from ai_company.memory.protocol import MemoryBackend  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.memory import MEMORY_BACKEND_UNKNOWN

logger = get_logger(__name__)


def create_memory_backend(config: CompanyMemoryConfig) -> MemoryBackend:
    """Create a memory backend from configuration.

    Currently a placeholder — raises ``MemoryConfigError`` for all
    backends.  Concrete registration happens in #41.

    Args:
        config: Memory configuration (includes backend selection and
            backend-specific settings).

    Returns:
        A new, disconnected backend instance.  Currently unreachable
        — the function always raises while the Mem0 adapter (#41)
        is pending.

    Raises:
        MemoryConfigError: If the backend is not yet implemented or
            not recognized.
    """
    if config.backend == "mem0":
        msg = "mem0 backend not yet implemented"
        logger.warning(
            MEMORY_BACKEND_UNKNOWN,
            backend="mem0",
            reason=msg,
        )
        raise MemoryConfigError(msg)
    # Defensive guard: config validation rejects unknown backends, so
    # this branch is unreachable under normal construction.  It exists
    # as a safety net for callers that bypass validation (e.g. via
    # model_construct).
    msg = f"Unknown memory backend: {config.backend!r}"
    logger.error(MEMORY_BACKEND_UNKNOWN, backend=config.backend)
    raise MemoryConfigError(msg)
