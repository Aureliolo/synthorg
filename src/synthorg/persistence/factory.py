"""Factory for creating persistence backends from configuration.

Each company gets its own ``PersistenceBackend`` instance, which maps
to its own database.  This enables multi-tenancy: one database per
company, selectable via the ``PersistenceConfig`` embedded in each
company's ``RootConfig``.
"""

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_CREATED,
    PERSISTENCE_BACKEND_UNKNOWN,
)
from synthorg.observability.redaction import safe_error_description
from synthorg.persistence.config import PersistenceConfig  # noqa: TC001
from synthorg.persistence.protocol import PersistenceBackend  # noqa: TC001
from synthorg.persistence.registry import PersistenceBackendRegistry
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

logger = get_logger(__name__)


def _build_sqlite(config: PersistenceConfig) -> PersistenceBackend:
    """Construct sqlite.

    Returns:
        Result of type ``PersistenceBackend``.
    """
    backend = SQLitePersistenceBackend(config.sqlite)
    logger.debug(
        PERSISTENCE_BACKEND_CREATED,
        backend="sqlite",
        path=config.sqlite.path,
    )
    return backend


def _build_postgres(config: PersistenceConfig) -> PersistenceBackend:
    """Construct postgres.

    Returns:
        Result of type ``PersistenceBackend``.

    Raises:
        PersistenceConnectionError: If the connection pool is unavailable.
    """
    if config.postgres is None:
        msg = "backend='postgres' requires a PostgresConfig"
        logger.error(PERSISTENCE_BACKEND_UNKNOWN, backend=config.backend)
        raise PersistenceConnectionError(msg)
    try:
        from synthorg.persistence.postgres.backend import (  # noqa: PLC0415
            PostgresPersistenceBackend,
        )
    except ImportError as exc:
        msg = (
            "Postgres backend requires the 'postgres' extra. "
            "Install with: uv pip install 'synthorg[postgres]'"
        )
        logger.warning(
            PERSISTENCE_BACKEND_UNKNOWN,
            backend=config.backend,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise PersistenceConnectionError(msg) from exc
    backend = PostgresPersistenceBackend(config.postgres)
    logger.debug(
        PERSISTENCE_BACKEND_CREATED,
        backend="postgres",
        host=config.postgres.host,
        database=config.postgres.database,
    )
    return backend


_REGISTRY: PersistenceBackendRegistry = PersistenceBackendRegistry(
    {
        "sqlite": _build_sqlite,
        "postgres": _build_postgres,
    },
)


def default_registry() -> PersistenceBackendRegistry:
    """Return the module-level registry containing the built-in backends.

    Returns:
        Result of type ``PersistenceBackendRegistry``.
    """
    return _REGISTRY


def create_backend(config: PersistenceConfig) -> PersistenceBackend:
    """Create a persistence backend from configuration.

    Factory function that maps ``config.backend`` to the correct
    concrete backend class via :class:`PersistenceBackendRegistry`.
    Each call returns a new, disconnected backend instance -- the
    caller is responsible for calling ``connect()`` and ``migrate()``.

    Args:
        config: Persistence configuration (includes backend selection
            and backend-specific settings).

    Returns:
        A new, disconnected backend instance.

    Raises:
        PersistenceConnectionError: If the backend name is not
            registered, the optional dependency is missing, or
            backend-specific configuration is absent.

    Example::

        config = PersistenceConfig(
            backend="sqlite",
            sqlite=SQLiteConfig(path="data/company-a.db"),
        )
        backend = create_backend(config)
        await backend.connect()
        await backend.migrate()
    """
    try:
        return _REGISTRY.build(config)
    except StrategyFactoryNotFoundError as exc:
        msg = f"Unknown persistence backend: {config.backend!r}"
        logger.warning(
            PERSISTENCE_BACKEND_UNKNOWN,
            backend=config.backend,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise PersistenceConnectionError(msg) from exc
