# module-kind: declarative
"""Persistence event constants for the lifecycle-transition sub-domain."""

from typing import Final

PERSISTENCE_LIFECYCLE_TRANSITION_SAVE_FAILED: Final[str] = (
    "persistence.lifecycle_transition.save_failed"
)
PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED: Final[str] = (
    "persistence.lifecycle_transition.query_failed"
)
