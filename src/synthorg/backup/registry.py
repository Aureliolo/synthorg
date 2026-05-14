"""Backup handler registry for backend-pluggable persistence backups.

``StrategyRegistry`` keyed on the persistence backend discriminator;
adding a new backend means registering its handler factory here rather
than editing the dispatch site.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from synthorg.backup.errors import BackupConfigurationError
from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)
from synthorg.core.registry.strategy import StrategyRegistry
from synthorg.observability import get_logger
from synthorg.observability.events.backup import BACKUP_HANDLER_REGISTRATION_FAILED
from synthorg.persistence.postgres.backup_utils import ensure_pg_tools_available

if TYPE_CHECKING:
    from synthorg.backup.handlers.protocol import ComponentHandler
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)


def _build_sqlite_handler(
    config: RootConfig,
    *,
    resolved_db_path: object,
) -> ComponentHandler:
    """Construct a SQLite persistence backup handler from RootConfig.

    No ``None`` guard on ``config.persistence.sqlite`` because the
    schema declares ``sqlite: SQLiteConfig`` with
    ``default_factory=SQLiteConfig`` (see
    :class:`synthorg.persistence.config.PersistenceConfig`), so
    Pydantic always materialises a value even when the YAML omits the
    block.
    """
    from pathlib import Path  # noqa: PLC0415

    db_path = resolved_db_path or Path(config.persistence.sqlite.path)
    if not isinstance(db_path, Path):
        db_path = Path(str(db_path))
    return SQLitePersistenceComponentHandler(db_path=db_path)


def _build_postgres_handler(
    config: RootConfig,
    *,
    resolved_db_path: object,  # noqa: ARG001 -- unused, parity with sqlite signature
) -> ComponentHandler:
    """Construct a Postgres persistence backup handler from RootConfig.

    Verifies ``pg_dump`` and ``pg_restore`` are on PATH before
    constructing the handler so missing tooling surfaces at factory
    dispatch (the ``BACKUP_HANDLER_REGISTRATION_FAILED`` event) rather
    than the first scheduled backup attempt.
    """
    pg_config = config.persistence.postgres
    if pg_config is None:
        msg = (
            "persistence.backend is 'postgres' but persistence.postgres is "
            "None; supply Postgres connection details to enable backup."
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


type _PersistenceHandlerFactory = Callable[..., "ComponentHandler"]


PERSISTENCE_BACKUP_HANDLER_REGISTRY: StrategyRegistry[ComponentHandler] = (
    StrategyRegistry(
        {
            "sqlite": _build_sqlite_handler,
            "postgres": _build_postgres_handler,
        },
        kind="persistence_backup_handler",
    )
)
