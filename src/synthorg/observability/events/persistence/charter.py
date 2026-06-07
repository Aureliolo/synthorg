# module-kind: declarative
"""Persistence event constants for the charter sub-domain."""

from typing import Final

# Read/query markers + failure path only; the persistence-boundary gate forbids
# repos from emitting mutation lifecycle events (the service layer owns the
# charter-status audit hop).
PERSISTENCE_CHARTER_FETCHED: Final[str] = "persistence.charter.fetched"
PERSISTENCE_CHARTER_LISTED: Final[str] = "persistence.charter.listed"
PERSISTENCE_CHARTER_FAILED: Final[str] = "persistence.charter.failed"
PERSISTENCE_CHARTER_UNKNOWN_BACKEND: Final[str] = "persistence.charter.unknown_backend"
PERSISTENCE_CHARTER_HANDLE_UNAVAILABLE: Final[str] = (
    "persistence.charter.handle_unavailable"
)
