# module-kind: declarative
"""Persistence event constants for the conversation sub-domain.

Failure paths only: the persistence-boundary gate forbids repos from emitting
their own mutation lifecycle (_SAVED / _DELETED) events; the service layer owns
the audit hop. FETCHED / LISTED are debug-level read markers, which the gate does
not consider mutations.
"""

from typing import Final

PERSISTENCE_CONVERSATION_FETCHED: Final[str] = "persistence.conversation.fetched"
PERSISTENCE_CONVERSATION_LISTED: Final[str] = "persistence.conversation.listed"
PERSISTENCE_CONVERSATION_FAILED: Final[str] = "persistence.conversation.failed"
