# module-kind: declarative
"""Persistence event constants for the agent_contribution sub-domain."""

from typing import Final

PERSISTENCE_AGENT_CONTRIBUTION_APPENDED: Final[str] = (
    "persistence.agent_contribution.appended"
)
PERSISTENCE_AGENT_CONTRIBUTION_APPEND_FAILED: Final[str] = (
    "persistence.agent_contribution.append_failed"
)
PERSISTENCE_AGENT_CONTRIBUTION_QUERIED: Final[str] = (
    "persistence.agent_contribution.queried"
)
PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED: Final[str] = (
    "persistence.agent_contribution.query_failed"
)
PERSISTENCE_AGENT_CONTRIBUTION_DESERIALIZE_FAILED: Final[str] = (
    "persistence.agent_contribution.deserialize_failed"
)
