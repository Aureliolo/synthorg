"""Factory for creating persistence backends from configuration.

Each company gets its own ``PersistenceBackend`` instance, which maps
to its own database.  This enables multi-tenancy: one database per
company, selectable via the ``PersistenceConfig`` embedded in each
company's ``RootConfig``.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_CREATED,
    PERSISTENCE_BACKEND_UNKNOWN,
)
from synthorg.persistence.config import PersistenceConfig  # noqa: TC001
from synthorg.persistence.errors import PersistenceConnectionError
from synthorg.persistence.protocol import PersistenceBackend  # noqa: TC001
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

logger = get_logger(__name__)


def create_backend(config: PersistenceConfig) -> PersistenceBackend:
    """Create a persistence backend from configuration.

    Factory function that maps ``config.backend`` to the correct
    concrete backend class.  Each call returns a new, disconnected
    backend instance — the caller is responsible for calling
    ``connect()`` and ``migrate()``.

    Args:
        config: Persistence configuration (includes backend selection
            and backend-specific settings).

    Returns:
        A new, disconnected backend instance.

    Raises:
        PersistenceConnectionError: If the backend name is not
            recognized.

    Example::

        config = PersistenceConfig(
            backend="sqlite",
            sqlite=SQLiteConfig(path="data/company-a.db"),
        )
        backend = create_backend(config)
        await backend.connect()
        await backend.migrate()
    """
    if config.backend == "sqlite":
        backend: PersistenceBackend = SQLitePersistenceBackend(config.sqlite)
        logger.debug(
            PERSISTENCE_BACKEND_CREATED,
            backend="sqlite",
            path=config.sqlite.path,
        )
        return backend
    msg = f"Unknown persistence backend: {config.backend!r}"
    logger.error(PERSISTENCE_BACKEND_UNKNOWN, backend=config.backend)
    raise PersistenceConnectionError(msg)
