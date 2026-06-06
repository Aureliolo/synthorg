# module-kind: declarative
"""Persistence event constants for the agent_state sub-domain."""

from typing import Final

PERSISTENCE_AGENT_STATE_SAVED: Final[str] = "persistence.agent_state.saved"
PERSISTENCE_AGENT_STATE_SAVE_FAILED: Final[str] = "persistence.agent_state.save_failed"
PERSISTENCE_AGENT_STATE_FETCHED: Final[str] = "persistence.agent_state.fetched"
PERSISTENCE_AGENT_STATE_FETCH_FAILED: Final[str] = (
    "persistence.agent_state.fetch_failed"
)
PERSISTENCE_AGENT_STATE_NOT_FOUND: Final[str] = "persistence.agent_state.not_found"
PERSISTENCE_AGENT_STATE_ACTIVE_QUERIED: Final[str] = (
    "persistence.agent_state.active_queried"
)
PERSISTENCE_AGENT_STATE_ACTIVE_QUERY_FAILED: Final[str] = (
    "persistence.agent_state.active_query_failed"
)
PERSISTENCE_AGENT_STATE_LISTED: Final[str] = "persistence.agent_state.listed"
PERSISTENCE_AGENT_STATE_LIST_FAILED: Final[str] = "persistence.agent_state.list_failed"
PERSISTENCE_AGENT_STATE_DELETED: Final[str] = "persistence.agent_state.deleted"
PERSISTENCE_AGENT_STATE_DELETE_FAILED: Final[str] = (
    "persistence.agent_state.delete_failed"
)
PERSISTENCE_AGENT_STATE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.agent_state.deserialize_failed"
)
