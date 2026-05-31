"""Backend-agnostic row -> model marshalling for conversational state.

Both the SQLite and Postgres conversation repositories deserialise the
same ``conversations`` / ``conversation_turns`` columns into the same
domain models. The row objects differ (``aiosqlite.Row`` vs psycopg
``dict_row``) but both support string-key indexing, so this module's
``RowLike`` protocol lets one marshaller serve both backends -- the
timestamp coercer normalises ``TEXT`` / ``TIMESTAMPTZ`` alike.
"""

from typing import Any, Protocol, runtime_checkable

from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.persistence_errors import QueryError
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONVERSATION_FAILED,
    PERSISTENCE_CONVERSATION_TURN_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp

logger = get_logger(__name__)


@runtime_checkable
class RowLike(Protocol):
    """A database row supporting string-key access (dict / sqlite Row)."""

    def __getitem__(self, key: str) -> Any: ...


def row_to_conversation(row: RowLike) -> Conversation:
    """Convert a database row into a :class:`Conversation`.

    Returns:
        Result of type ``Conversation``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return Conversation(
            id=str(row["id"]),
            created_by=str(row["created_by"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            status=ConversationStatus(str(row["status"])),
            kind=ConversationKind(str(row["kind"])),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation row"
        logger.warning(
            PERSISTENCE_CONVERSATION_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def row_to_turn(row: RowLike) -> ConversationTurn:
    """Convert a database row into a :class:`ConversationTurn`.

    Returns:
        Result of type ``ConversationTurn``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        author_agent_id = row["author_agent_id"]
        author_name = row["author_name"]
        routed_topic = row["routed_topic"]
        routing_confidence = row["routing_confidence"]
        return ConversationTurn(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            sequence=int(row["sequence"]),
            role=ConversationRole(str(row["role"])),
            content=str(row["content"]),
            author_agent_id=(None if author_agent_id is None else str(author_agent_id)),
            author_name=None if author_name is None else str(author_name),
            routed_topic=None if routed_topic is None else str(routed_topic),
            routing_confidence=(
                None if routing_confidence is None else float(routing_confidence)
            ),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation turn row"
        logger.warning(
            PERSISTENCE_CONVERSATION_TURN_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


__all__ = ["RowLike", "row_to_conversation", "row_to_turn"]
