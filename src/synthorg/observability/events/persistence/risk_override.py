# module-kind: declarative
"""Persistence event constants for the risk_override sub-domain."""

from typing import Final

PERSISTENCE_RISK_OVERRIDE_SAVED: Final[str] = "persistence.risk_override.saved"
PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED: Final[str] = (
    "persistence.risk_override.save_failed"
)
PERSISTENCE_RISK_OVERRIDE_REVOKE_FAILED: Final[str] = (
    "persistence.risk_override.revoke_failed"
)
PERSISTENCE_RISK_OVERRIDE_DELETE_FAILED: Final[str] = (
    "persistence.risk_override.delete_failed"
)
PERSISTENCE_RISK_OVERRIDE_QUERIED: Final[str] = "persistence.risk_override.queried"
PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED: Final[str] = (
    "persistence.risk_override.query_failed"
)
