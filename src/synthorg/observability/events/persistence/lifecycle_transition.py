# module-kind: declarative
"""Persistence event constants for the lifecycle-transition sub-domain.

Named for the operations the append-only store actually exposes, matching
its sibling :mod:`.audit_chain_entry`: a store whose only write is
``append`` reporting a ``save_failed`` sends anyone grepping for the write
path looking for a method that does not exist.
"""

from typing import Final

PERSISTENCE_LIFECYCLE_TRANSITION_APPENDED: Final[str] = (
    "persistence.lifecycle_transition.appended"
)
PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_FAILED: Final[str] = (
    "persistence.lifecycle_transition.append_failed"
)
PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_RETRIED: Final[str] = (
    "persistence.lifecycle_transition.append_retried"
)
PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_RECOVERED: Final[str] = (
    "persistence.lifecycle_transition.append_recovered"
)
PERSISTENCE_LIFECYCLE_TRANSITION_QUERIED: Final[str] = (
    "persistence.lifecycle_transition.queried"
)
PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED: Final[str] = (
    "persistence.lifecycle_transition.query_failed"
)
PERSISTENCE_LIFECYCLE_TRANSITION_DESERIALIZE_FAILED: Final[str] = (
    "persistence.lifecycle_transition.deserialize_failed"
)
PERSISTENCE_LIFECYCLE_TRANSITION_PURGE_FAILED: Final[str] = (
    "persistence.lifecycle_transition.purge_failed"
)
