"""SQLite repositories for conversational clarify-and-propose state.

``SQLiteConversationRepository`` (in ``_header``) backs the conversation
header (id-keyed CRUD + atomic status compare-and-set);
``SQLiteConversationTurnRepository`` (in ``_turns``) backs the immutable
ordered turns (append + filtered query + retention purge). Both satisfy
the protocols in ``persistence/conversation_protocol.py`` structurally.
Split by entity so each module stays under the repository LOC cap.
"""

from synthorg.persistence.sqlite.conversation_repo._header import (
    SQLiteConversationRepository,
)
from synthorg.persistence.sqlite.conversation_repo._turns import (
    SQLiteConversationTurnRepository,
)

__all__ = [
    "SQLiteConversationRepository",
    "SQLiteConversationTurnRepository",
]
