# module-kind: declarative
"""Persistence event constants for the audit_chain_entry sub-domain."""

from typing import Final

PERSISTENCE_AUDIT_CHAIN_ENTRY_APPENDED: Final[str] = (
    "persistence.audit_chain_entry.appended"
)
PERSISTENCE_AUDIT_CHAIN_ENTRY_APPEND_FAILED: Final[str] = (
    "persistence.audit_chain_entry.append_failed"
)
PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERIED: Final[str] = (
    "persistence.audit_chain_entry.queried"
)
PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERY_FAILED: Final[str] = (
    "persistence.audit_chain_entry.query_failed"
)
PERSISTENCE_AUDIT_CHAIN_ENTRY_DESERIALIZE_FAILED: Final[str] = (
    "persistence.audit_chain_entry.deserialize_failed"
)
PERSISTENCE_AUDIT_CHAIN_ENTRY_PURGE_FAILED: Final[str] = (
    "persistence.audit_chain_entry.purge_failed"
)
