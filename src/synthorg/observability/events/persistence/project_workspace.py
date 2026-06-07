# module-kind: declarative
"""Persistence event constants for the project_workspace sub-domain."""

from typing import Final

PERSISTENCE_PROJECT_WORKSPACE_SAVE_FAILED: Final[str] = (
    "persistence.project_workspace.save_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_FETCHED: Final[str] = (
    "persistence.project_workspace.fetched"
)
PERSISTENCE_PROJECT_WORKSPACE_FETCH_FAILED: Final[str] = (
    "persistence.project_workspace.fetch_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_LISTED: Final[str] = (
    "persistence.project_workspace.listed"
)
PERSISTENCE_PROJECT_WORKSPACE_LIST_FAILED: Final[str] = (
    "persistence.project_workspace.list_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED: Final[str] = (
    "persistence.project_workspace.delete_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_workspace.deserialize_failed"
)
