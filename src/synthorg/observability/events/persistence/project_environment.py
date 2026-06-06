# module-kind: declarative
"""Persistence event constants for the project_environment sub-domain."""

from typing import Final

PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED: Final[str] = (
    "persistence.project_environment.save_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_FETCHED: Final[str] = (
    "persistence.project_environment.fetched"
)
PERSISTENCE_PROJECT_ENVIRONMENT_FETCH_FAILED: Final[str] = (
    "persistence.project_environment.fetch_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_LISTED: Final[str] = (
    "persistence.project_environment.listed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED: Final[str] = (
    "persistence.project_environment.list_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED: Final[str] = (
    "persistence.project_environment.delete_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_environment.deserialize_failed"
)
