"""Pluggable persistence layer for operational data (DESIGN_SPEC §7.5).

Re-exports the protocol, repository protocols, config models, factory,
and error hierarchy so consumers can import from ``ai_company.persistence``
directly.
"""

from ai_company.persistence.config import PersistenceConfig, SQLiteConfig
from ai_company.persistence.errors import (
    DuplicateRecordError,
    MigrationError,
    PersistenceConnectionError,
    PersistenceError,
    QueryError,
    RecordNotFoundError,
)
from ai_company.persistence.factory import create_backend
from ai_company.persistence.protocol import PersistenceBackend
from ai_company.persistence.repositories import (
    CostRecordRepository,
    MessageRepository,
    TaskRepository,
)

__all__ = [
    "CostRecordRepository",
    "DuplicateRecordError",
    "MessageRepository",
    "MigrationError",
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
