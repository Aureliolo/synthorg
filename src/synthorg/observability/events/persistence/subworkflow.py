# module-kind: declarative
"""Persistence event constants for the subworkflow sub-domain."""

from typing import Final

PERSISTENCE_SUBWORKFLOW_SAVED: Final[str] = "persistence.subworkflow.saved"
PERSISTENCE_SUBWORKFLOW_SAVE_FAILED: Final[str] = "persistence.subworkflow.save_failed"
PERSISTENCE_SUBWORKFLOW_FETCHED: Final[str] = "persistence.subworkflow.fetched"
PERSISTENCE_SUBWORKFLOW_FETCH_FAILED: Final[str] = (
    "persistence.subworkflow.fetch_failed"
)
PERSISTENCE_SUBWORKFLOW_LISTED: Final[str] = "persistence.subworkflow.listed"
PERSISTENCE_SUBWORKFLOW_LIST_FAILED: Final[str] = "persistence.subworkflow.list_failed"
PERSISTENCE_SUBWORKFLOW_DELETED: Final[str] = "persistence.subworkflow.deleted"
PERSISTENCE_SUBWORKFLOW_DELETE_FAILED: Final[str] = (
    "persistence.subworkflow.delete_failed"
)
PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED: Final[str] = (
    "persistence.subworkflow.deserialize_failed"
)
