# module-kind: declarative
"""Persistence event constants for the workflow_exec sub-domain."""

from typing import Final

PERSISTENCE_WORKFLOW_EXEC_SAVED: Final[str] = "persistence.workflow_exec.saved"
PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED: Final[str] = (
    "persistence.workflow_exec.save_failed"
)
PERSISTENCE_WORKFLOW_EXEC_FETCHED: Final[str] = "persistence.workflow_exec.fetched"
PERSISTENCE_WORKFLOW_EXEC_FETCH_FAILED: Final[str] = (
    "persistence.workflow_exec.fetch_failed"
)
PERSISTENCE_WORKFLOW_EXEC_LISTED: Final[str] = "persistence.workflow_exec.listed"
PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED: Final[str] = (
    "persistence.workflow_exec.list_failed"
)
PERSISTENCE_WORKFLOW_EXEC_DELETED: Final[str] = "persistence.workflow_exec.deleted"
PERSISTENCE_WORKFLOW_EXEC_DELETE_FAILED: Final[str] = (
    "persistence.workflow_exec.delete_failed"
)
PERSISTENCE_WORKFLOW_EXEC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.workflow_exec.deserialize_failed"
)
PERSISTENCE_WORKFLOW_EXEC_FOUND_BY_TASK: Final[str] = (
    "persistence.workflow_exec.found_by_task"
)
PERSISTENCE_WORKFLOW_EXEC_FIND_BY_TASK_FAILED: Final[str] = (
    "persistence.workflow_exec.find_by_task_failed"
)
