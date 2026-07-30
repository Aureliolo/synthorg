"""Backup handler registry for backend-pluggable persistence backups.

``StrategyRegistry`` keyed on the persistence backend discriminator;
adding a new backend means registering its handler factory here rather
than editing the dispatch site.
"""

from synthorg.backup.errors import BackupConfigurationError
from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.handlers.protocol import ComponentHandler
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)
from synthorg.config.schema import RootConfig
from synthorg.core.registry.strategy import StrategyRegistry
from synthorg.observability import get_logger
from synthorg.observability.events.backup import BACKUP_HANDLER_REGISTRATION_FAILED
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.postgres.backup_utils import ensure_pg_tools_available

logger = get_logger(__name__)


def _build_sqlite_handler(
    config: RootConfig,
    *,
    resolved_db_path: object,
    connected_config: SQLiteConfig | PostgresConfig | None = None,
) -> ComponentHandler:
    """Construct a SQLite persistence backup handler from RootConfig.

    No ``None`` guard on ``config.persistence.sqlite`` because the
    schema declares ``sqlite: SQLiteConfig`` with
    ``default_factory=SQLiteConfig`` (see
    :class:`synthorg.persistence.config.PersistenceConfig`), so
    Pydantic always materialises a value even when the YAML omits the
    block.

    Returns:
        A SQLite persistence ``ComponentHandler`` bound to the resolved
        database path.
    """
    from pathlib import Path  # noqa: PLC0415

    connected_path = (
        connected_config.path if isinstance(connected_config, SQLiteConfig) else None
    )
    db_path = resolved_db_path or connected_path or config.persistence.sqlite.path
    if not isinstance(db_path, Path):
        db_path = Path(str(db_path))
    return SQLitePersistenceComponentHandler(db_path=db_path)


def _build_postgres_handler(
    config: RootConfig,
    *,
    resolved_db_path: object,  # noqa: ARG001 -- unused, parity with sqlite signature
    connected_config: SQLiteConfig | PostgresConfig | None = None,
) -> ComponentHandler:
    """Construct a Postgres persistence backup handler.

    Prefers the connection details of the backend that was actually
    built over ``config.persistence.postgres``. An env-driven boot
    (``SYNTHORG_DATABASE_URL``) parses its own config in
    ``api/boot_persistence`` and never writes it back into
    ``RootConfig``, whose ``postgres`` block then stays ``None`` (or,
    worse, describes a stale database that a dump would silently
    succeed against).

    Verifies ``pg_dump`` and ``pg_restore`` are on PATH before
    constructing the handler so missing tooling surfaces at factory
    dispatch (the ``BACKUP_HANDLER_REGISTRATION_FAILED`` event) rather
    than the first scheduled backup attempt.

    Returns:
        A Postgres persistence ``ComponentHandler``.

    Raises:
        BackupConfigurationError: When no Postgres connection details
            are available from either source.
        PgToolUnavailableError: When ``pg_dump`` / ``pg_restore`` are
            not available on PATH.
    """
    pg_config = (
        connected_config
        if isinstance(connected_config, PostgresConfig)
        else config.persistence.postgres
    )
    if pg_config is None:
        msg = (
            "Postgres backup requires connection details, but neither the "
            "connected backend nor persistence.postgres supplied any."
        )
        logger.error(
            BACKUP_HANDLER_REGISTRATION_FAILED,
            backend="postgres",
            error_type="BackupConfigurationError",
            error=msg,
        )
        raise BackupConfigurationError(msg)
    ensure_pg_tools_available()
    return PostgresPersistenceComponentHandler(config=pg_config)


PERSISTENCE_BACKUP_HANDLER_REGISTRY: StrategyRegistry[ComponentHandler] = (
    StrategyRegistry(
        {
            "sqlite": _build_sqlite_handler,
            "postgres": _build_postgres_handler,
        },
        kind="persistence_backup_handler",
    )
)
