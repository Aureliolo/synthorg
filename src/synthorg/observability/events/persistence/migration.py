# module-kind: declarative
"""Persistence event constants for the migration sub-domain."""

from typing import Final

PERSISTENCE_MIGRATION_STARTED: Final[str] = "persistence.migration.started"
PERSISTENCE_MIGRATION_COMPLETED: Final[str] = "persistence.migration.completed"
PERSISTENCE_MIGRATION_FAILED: Final[str] = "persistence.migration.failed"
