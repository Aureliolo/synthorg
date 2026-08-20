"""Postgres repositories for conversational clarify-and-propose state.

Siblings of the SQLite implementations, backed by
``psycopg_pool.AsyncConnectionPool``. ``TIMESTAMPTZ`` columns return
native ``datetime`` objects; the shared timestamp coercer normalises
them (and any legacy ISO strings) to UTC-aware values. Satisfy the
``conversation_protocol`` protocols structurally.

``PostgresConversationRepository`` (in ``_header``) backs the conversation
header; ``PostgresConversationTurnRepository`` (in ``_turns``) backs the
immutable ordered turns. Split by entity, mirroring the SQLite package, so
each module stays under the repository LOC cap.
"""

from synthorg.persistence.postgres.conversation_repo._header import (
    PostgresConversationRepository,
)
from synthorg.persistence.postgres.conversation_repo._turns import (
    PostgresConversationTurnRepository,
)

__all__ = [
    "PostgresConversationRepository",
    "PostgresConversationTurnRepository",
]
