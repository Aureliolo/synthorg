"""Component backup handlers."""

from synthorg.backup.handlers.config_handler import ConfigComponentHandler
from synthorg.backup.handlers.memory import MemoryComponentHandler
from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.handlers.protocol import ComponentHandler
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)

__all__ = [
    "ComponentHandler",
    "ConfigComponentHandler",
    "MemoryComponentHandler",
    "PostgresPersistenceComponentHandler",
    "SQLitePersistenceComponentHandler",
]
