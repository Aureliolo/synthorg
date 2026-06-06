# module-kind: declarative
"""Persistence event constants for the knowledge_source sub-domain."""

from typing import Final

PERSISTENCE_KNOWLEDGE_SOURCE_SAVE_FAILED: Final[str] = (
    "persistence.knowledge_source.save_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_FETCHED: Final[str] = (
    "persistence.knowledge_source.fetched"
)
PERSISTENCE_KNOWLEDGE_SOURCE_FETCH_FAILED: Final[str] = (
    "persistence.knowledge_source.fetch_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_LISTED: Final[str] = "persistence.knowledge_source.listed"
PERSISTENCE_KNOWLEDGE_SOURCE_LIST_FAILED: Final[str] = (
    "persistence.knowledge_source.list_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_QUERIED: Final[str] = (
    "persistence.knowledge_source.queried"
)
PERSISTENCE_KNOWLEDGE_SOURCE_QUERY_FAILED: Final[str] = (
    "persistence.knowledge_source.query_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_COUNTED: Final[str] = (
    "persistence.knowledge_source.counted"
)
PERSISTENCE_KNOWLEDGE_SOURCE_COUNT_FAILED: Final[str] = (
    "persistence.knowledge_source.count_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED: Final[str] = (
    "persistence.knowledge_source.delete_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.knowledge_source.deserialize_failed"
)
