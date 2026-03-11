"""Pluggable persistence layer for operational data (see Memory design page).

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
