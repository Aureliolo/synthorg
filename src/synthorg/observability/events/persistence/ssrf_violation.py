# module-kind: declarative
"""Persistence event constants for the ssrf_violation sub-domain."""

from typing import Final

PERSISTENCE_SSRF_VIOLATION_SAVED: Final[str] = "persistence.ssrf_violation.saved"
PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED: Final[str] = (
    "persistence.ssrf_violation.save_failed"
)
PERSISTENCE_SSRF_VIOLATION_STATUS_UPDATED: Final[str] = (
    "persistence.ssrf_violation.status_updated"
)
PERSISTENCE_SSRF_VIOLATION_QUERIED: Final[str] = "persistence.ssrf_violation.queried"
PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED: Final[str] = (
    "persistence.ssrf_violation.query_failed"
)
