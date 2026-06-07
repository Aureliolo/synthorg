# module-kind: declarative
"""Persistence event constants for the circuit_breaker sub-domain."""

from typing import Final

PERSISTENCE_CIRCUIT_BREAKER_SAVED: Final[str] = "persistence.circuit_breaker.saved"
PERSISTENCE_CIRCUIT_BREAKER_SAVE_FAILED: Final[str] = (
    "persistence.circuit_breaker.save_failed"
)
PERSISTENCE_CIRCUIT_BREAKER_LOADED: Final[str] = "persistence.circuit_breaker.loaded"
PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED: Final[str] = (
    "persistence.circuit_breaker.load_failed"
)
PERSISTENCE_CIRCUIT_BREAKER_DELETED: Final[str] = "persistence.circuit_breaker.deleted"
PERSISTENCE_CIRCUIT_BREAKER_DELETE_FAILED: Final[str] = (
    "persistence.circuit_breaker.delete_failed"
)
