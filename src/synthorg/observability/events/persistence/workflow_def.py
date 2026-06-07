# module-kind: declarative
"""Persistence event constants for the workflow_def sub-domain."""

from typing import Final

PERSISTENCE_WORKFLOW_DEF_SAVED: Final[str] = "persistence.workflow_def.saved"
PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED: Final[str] = (
    "persistence.workflow_def.save_failed"
)
PERSISTENCE_WORKFLOW_DEF_FETCHED: Final[str] = "persistence.workflow_def.fetched"
PERSISTENCE_WORKFLOW_DEF_FETCH_FAILED: Final[str] = (
    "persistence.workflow_def.fetch_failed"
)
PERSISTENCE_WORKFLOW_DEF_LISTED: Final[str] = "persistence.workflow_def.listed"
PERSISTENCE_WORKFLOW_DEF_LIST_FAILED: Final[str] = (
    "persistence.workflow_def.list_failed"
)
PERSISTENCE_WORKFLOW_DEF_DELETED: Final[str] = "persistence.workflow_def.deleted"
PERSISTENCE_WORKFLOW_DEF_DELETE_FAILED: Final[str] = (
    "persistence.workflow_def.delete_failed"
)
PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED: Final[str] = (
    "persistence.workflow_def.deserialize_failed"
)
