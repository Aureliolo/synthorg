"""SQLite persistence backend (DESIGN_SPEC §7.5 — initial backend)."""

from ai_company.persistence.sqlite.backend import SQLitePersistenceBackend
from ai_company.persistence.sqlite.migrations import (
    SCHEMA_VERSION,
    run_migrations,
)
from ai_company.persistence.sqlite.repositories import (
    SQLiteCostRecordRepository,
    SQLiteMessageRepository,
    SQLiteTaskRepository,
)

__all__ = [
    "SCHEMA_VERSION",
    "SQLiteCostRecordRepository",
    "SQLiteMessageRepository",
    "SQLitePersistenceBackend",
    "SQLiteTaskRepository",
    "run_migrations",
]
