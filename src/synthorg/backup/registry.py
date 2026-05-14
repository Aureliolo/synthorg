"""Backup handler registry for backend-pluggable persistence backups.

Replaces the hardcoded ``PersistenceComponentHandler(db_path=...)`` call
in ``backup/factory.py`` with a ``StrategyRegistry`` keyed on the
persistence backend discriminator. Adding a new backend means
registering its handler factory here rather than editing the dispatch
site.
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

if TYPE_CHECKING:
    from synthorg.backup.handlers.protocol import ComponentHandler
    from synthorg.config.schema import RootConfig


def _build_sqlite_handler(
    config: RootConfig,
    *,
    resolved_db_path: object,
) -> ComponentHandler:
    """Construct a SQLite persistence backup handler from RootConfig."""
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
    """Construct a Postgres persistence backup handler from RootConfig."""
    pg_config = config.persistence.postgres
    if pg_config is None:
        msg = (
            "persistence.backend is 'postgres' but persistence.postgres is "
            "None; supply Postgres connection details to enable backup."
        )
        raise BackupConfigurationError(msg)
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
