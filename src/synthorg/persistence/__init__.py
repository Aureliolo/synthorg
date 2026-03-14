"""Pluggable persistence layer for operational data (see Memory design page).

Re-exports the protocol, repository protocols, config models, factory,
and error hierarchy so consumers can import from ``synthorg.persistence``
directly.
"""

from synthorg.persistence.config import PersistenceConfig, SQLiteConfig
from synthorg.persistence.errors import (
    DuplicateRecordError,
    MigrationError,
    PersistenceConnectionError,
    PersistenceError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.repositories import (
    AuditRepository,
    CostRecordRepository,
    MessageRepository,
    ParkedContextRepository,
    TaskRepository,
)

__all__ = [
    "AuditRepository",
    "CostRecordRepository",
    "DuplicateRecordError",
    "MessageRepository",
    "MigrationError",
    "ParkedContextRepository",
    "PersistenceBackend",
    "PersistenceConfig",
    "PersistenceConnectionError",
    "PersistenceError",
    "QueryError",
    "RecordNotFoundError",
    "SQLiteConfig",
    "TaskRepository",
    "create_backend",
]
