"""Persistence event constants for the durable evolution-outcome log.

Constants follow the ``persistence.evolution_outcome.<action>`` naming
convention and are passed as the first argument to structured log calls.
"""

from typing import Final

PERSISTENCE_EVOLUTION_OUTCOME_SAVE_FAILED: Final[str] = (
    "persistence.evolution_outcome.save_failed"
)
PERSISTENCE_EVOLUTION_OUTCOME_QUERIED: Final[str] = (
    "persistence.evolution_outcome.queried"
)
PERSISTENCE_EVOLUTION_OUTCOME_QUERY_FAILED: Final[str] = (
    "persistence.evolution_outcome.query_failed"
)
PERSISTENCE_EVOLUTION_OUTCOME_DESERIALIZE_FAILED: Final[str] = (
    "persistence.evolution_outcome.deserialize_failed"
)
