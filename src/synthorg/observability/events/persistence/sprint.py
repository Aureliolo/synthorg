# module-kind: declarative
"""Persistence event constants for the sprint sub-domain."""

from typing import Final

# Read/query markers + failure path only; the persistence-boundary gate forbids
# repos from emitting mutation lifecycle events (the service layer owns the
# sprint-status audit hop).
PERSISTENCE_SPRINT_FETCHED: Final[str] = "persistence.sprint.fetched"
PERSISTENCE_SPRINT_LISTED: Final[str] = "persistence.sprint.listed"
PERSISTENCE_SPRINT_FAILED: Final[str] = "persistence.sprint.failed"
PERSISTENCE_SPRINT_UNKNOWN_BACKEND: Final[str] = "persistence.sprint.unknown_backend"
PERSISTENCE_SPRINT_HANDLE_UNAVAILABLE: Final[str] = (
    "persistence.sprint.handle_unavailable"
)
