"""Org memory backend factory.

Creates the appropriate ``OrgMemoryBackend`` implementation based on
configuration.
"""

from synthorg.memory.org.config import OrgMemoryConfig  # noqa: TC001
from synthorg.memory.org.errors import OrgMemoryConfigError
from synthorg.memory.org.hybrid_backend import HybridPromptRetrievalBackend
from synthorg.memory.org.protocol import OrgMemoryBackend  # noqa: TC001
from synthorg.memory.org.store import OrgFactStore  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.org_memory import (
    ORG_MEMORY_BACKEND_CREATED,
    ORG_MEMORY_CONFIG_INVALID,
)

logger = get_logger(__name__)


def create_org_memory_backend(
    config: OrgMemoryConfig,
    store: OrgFactStore,
) -> OrgMemoryBackend:
    """Create an org memory backend from configuration.

    Args:
        config: Org memory configuration.
        store: Extended facts store implementation.

    Returns:
        An ``OrgMemoryBackend`` implementation.

    Raises:
        OrgMemoryConfigError: If the backend name is unknown.
    """
    if config.backend == "hybrid_prompt_retrieval":
        backend = HybridPromptRetrievalBackend(
            core_policies=config.core_policies,
            store=store,
            access_config=config.write_access,
        )
        logger.info(
            ORG_MEMORY_BACKEND_CREATED,
            backend=config.backend,
            core_policy_count=len(config.core_policies),
        )
        return backend

    msg = (
        f"Unknown org memory backend {config.backend!r}. "
        f"Valid backends: ['hybrid_prompt_retrieval']"
    )
    logger.error(
        ORG_MEMORY_CONFIG_INVALID,
        backend=config.backend,
        reason=msg,
    )
    raise OrgMemoryConfigError(msg)
