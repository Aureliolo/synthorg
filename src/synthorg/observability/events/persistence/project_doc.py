# module-kind: declarative
"""Persistence event constants for the project_doc sub-domain (living docs)."""

from typing import Final

PERSISTENCE_PROJECT_DOC_SAVE_FAILED: Final[str] = "persistence.project_doc.save_failed"
PERSISTENCE_PROJECT_DOC_FETCHED: Final[str] = "persistence.project_doc.fetched"
PERSISTENCE_PROJECT_DOC_FETCH_FAILED: Final[str] = (
    "persistence.project_doc.fetch_failed"
)
PERSISTENCE_PROJECT_DOC_LISTED: Final[str] = "persistence.project_doc.listed"
PERSISTENCE_PROJECT_DOC_LIST_FAILED: Final[str] = "persistence.project_doc.list_failed"
PERSISTENCE_PROJECT_DOC_QUERIED: Final[str] = "persistence.project_doc.queried"
PERSISTENCE_PROJECT_DOC_QUERY_FAILED: Final[str] = (
    "persistence.project_doc.query_failed"
)
PERSISTENCE_PROJECT_DOC_COUNTED: Final[str] = "persistence.project_doc.counted"
PERSISTENCE_PROJECT_DOC_COUNT_FAILED: Final[str] = (
    "persistence.project_doc.count_failed"
)
PERSISTENCE_PROJECT_DOC_DELETE_FAILED: Final[str] = (
    "persistence.project_doc.delete_failed"
)
PERSISTENCE_PROJECT_DOC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_doc.deserialize_failed"
)
