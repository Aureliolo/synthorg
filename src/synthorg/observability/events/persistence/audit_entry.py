# module-kind: declarative
"""Persistence event constants for the audit_entry sub-domain."""

from typing import Final

PERSISTENCE_AUDIT_ENTRY_SAVED: Final[str] = "persistence.audit_entry.saved"
PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED: Final[str] = "persistence.audit_entry.save_failed"
PERSISTENCE_AUDIT_ENTRY_QUERIED: Final[str] = "persistence.audit_entry.queried"
PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED: Final[str] = (
    "persistence.audit_entry.query_failed"
)
PERSISTENCE_AUDIT_ENTRY_DESERIALIZE_FAILED: Final[str] = (
    "persistence.audit_entry.deserialize_failed"
)
