# module-kind: declarative
"""Persistence event constants for the research_run sub-domain."""

from typing import Final

PERSISTENCE_RESEARCH_RUN_SAVE_FAILED: Final[str] = (
    "persistence.research_run.save_failed"
)
PERSISTENCE_RESEARCH_RUN_FETCHED: Final[str] = "persistence.research_run.fetched"
PERSISTENCE_RESEARCH_RUN_FETCH_FAILED: Final[str] = (
    "persistence.research_run.fetch_failed"
)
PERSISTENCE_RESEARCH_RUN_LISTED: Final[str] = "persistence.research_run.listed"
PERSISTENCE_RESEARCH_RUN_LIST_FAILED: Final[str] = (
    "persistence.research_run.list_failed"
)
PERSISTENCE_RESEARCH_RUN_QUERIED: Final[str] = "persistence.research_run.queried"
PERSISTENCE_RESEARCH_RUN_QUERY_FAILED: Final[str] = (
    "persistence.research_run.query_failed"
)
PERSISTENCE_RESEARCH_RUN_COUNTED: Final[str] = "persistence.research_run.counted"
PERSISTENCE_RESEARCH_RUN_COUNT_FAILED: Final[str] = (
    "persistence.research_run.count_failed"
)
PERSISTENCE_RESEARCH_RUN_DELETE_FAILED: Final[str] = (
    "persistence.research_run.delete_failed"
)
PERSISTENCE_RESEARCH_RUN_DESERIALIZE_FAILED: Final[str] = (
    "persistence.research_run.deserialize_failed"
)
