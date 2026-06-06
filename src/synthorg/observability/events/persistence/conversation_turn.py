# module-kind: declarative
"""Persistence event constants for the conversation_turn sub-domain."""

from typing import Final

PERSISTENCE_CONVERSATION_TURN_APPENDED: Final[str] = (
    "persistence.conversation_turn.appended"
)
PERSISTENCE_CONVERSATION_TURN_QUERIED: Final[str] = (
    "persistence.conversation_turn.queried"
)
PERSISTENCE_CONVERSATION_TURN_FAILED: Final[str] = (
    "persistence.conversation_turn.failed"
)
