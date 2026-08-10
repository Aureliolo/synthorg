# module-kind: declarative
"""Persistence event constants for the deleted-entity sub-domain.

Named for the operations the append-only store actually exposes, matching
its sibling :mod:`.lifecycle_transition`.
"""

from typing import Final

PERSISTENCE_DELETED_ENTITY_APPENDED: Final[str] = "persistence.deleted_entity.appended"
PERSISTENCE_DELETED_ENTITY_APPEND_FAILED: Final[str] = (
    "persistence.deleted_entity.append_failed"
)
PERSISTENCE_DELETED_ENTITY_QUERIED: Final[str] = "persistence.deleted_entity.queried"
PERSISTENCE_DELETED_ENTITY_QUERY_FAILED: Final[str] = (
    "persistence.deleted_entity.query_failed"
)
PERSISTENCE_DELETED_ENTITY_DESERIALIZE_FAILED: Final[str] = (
    "persistence.deleted_entity.deserialize_failed"
)
PERSISTENCE_DELETED_ENTITY_PURGE_FAILED: Final[str] = (
    "persistence.deleted_entity.purge_failed"
)
