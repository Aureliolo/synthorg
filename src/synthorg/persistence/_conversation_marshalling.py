"""Backend-agnostic row -> model marshalling for conversational state.

Both the SQLite and Postgres conversation repositories deserialise the
same ``conversations`` / ``conversation_turns`` columns into the same
domain models. The row objects differ (``aiosqlite.Row`` vs psycopg
``dict_row``) but both support string-key indexing, so this module's
``RowLike`` protocol lets one marshaller serve both backends -- the
timestamp coercer normalises ``TEXT`` / ``TIMESTAMPTZ`` alike.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from synthorg.communication.conversation.enums import (
    ConversationalProposalStatus,
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationKind,
    ConversationParticipantStatus,
)
from synthorg.meta.chief_of_staff.group_models import (
    ConversationInvite,
    ConversationParticipant,
)
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_INVITE_FAILED,
    COS_GROUP_PARTICIPANT_FAILED,
)
from synthorg.observability.events.persistence.conversation import (
    PERSISTENCE_CONVERSATION_FAILED,
)
from synthorg.observability.events.persistence.conversation_turn import (
    PERSISTENCE_CONVERSATION_TURN_FAILED,
)
from synthorg.observability.events.persistence.conversational_proposal import (
    PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp

logger = get_logger(__name__)


@runtime_checkable
class RowLike(Protocol):
    """A database row supporting string-key access (dict / sqlite Row)."""

    # ``key`` is positional-only: the real rows this abstracts
    # (``dict`` and ``sqlite3.Row``) both expose a positional-only
    # ``__getitem__``, so a named parameter here would make neither a
    # structural match under runtime protocol checking.
    def __getitem__(self, key: str, /) -> object: ...


def _safe_row_id(row: RowLike) -> str:
    """Best-effort raw ``id`` for error context when marshalling fails.

    The marshallers catch a missing ``id`` column (``KeyError`` on
    dict rows, ``IndexError`` on ``sqlite3.Row``), so the ``id`` access
    in the error path must not itself raise. Returns a sentinel when the
    value is absent or unrenderable so the warning still pins the
    offending row where possible.

    Returns:
        The raw id as text, or ``"<unknown>"`` if it cannot be read.
    """
    try:
        return str(row["id"])
    except KeyError, IndexError, TypeError, ValueError:
        return "<unknown>"


def row_to_conversation(row: RowLike) -> Conversation:
    """Convert a database row into a :class:`Conversation`.

    Returns:
        Result of type ``Conversation``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return Conversation(
            id=UUID(str(row["id"])),
            created_by=str(row["created_by"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            status=ConversationStatus(str(row["status"])),
            kind=ConversationKind(str(row["kind"])),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        msg = "Failed to parse conversation row"
        logger.warning(
            PERSISTENCE_CONVERSATION_FAILED,
            operation="deserialize",
            row_id=_safe_row_id(row),
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
            id=UUID(str(row["id"])),
            conversation_id=str(row["conversation_id"]),
            sequence=int(str(row["sequence"])),
            role=ConversationRole(str(row["role"])),
            content=str(row["content"]),
            author_agent_id=(None if author_agent_id is None else str(author_agent_id)),
            author_name=None if author_name is None else str(author_name),
            routed_topic=None if routed_topic is None else str(routed_topic),
            routing_confidence=(
                None if routing_confidence is None else float(str(routing_confidence))
            ),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        msg = "Failed to parse conversation turn row"
        logger.warning(
            PERSISTENCE_CONVERSATION_TURN_FAILED,
            operation="deserialize",
            row_id=_safe_row_id(row),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def row_to_participant(row: RowLike) -> ConversationParticipant:
    """Convert a database row into a :class:`ConversationParticipant`.

    Returns:
        Result of type ``ConversationParticipant``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return ConversationParticipant(
            id=UUID(str(row["id"])),
            conversation_id=str(row["conversation_id"]),
            agent_id=str(row["agent_id"]),
            agent_name=str(row["agent_name"]),
            participant_role=str(row["participant_role"]),
            status=ConversationParticipantStatus(str(row["status"])),
            added_by=str(row["added_by"]),
            added_at=coerce_row_timestamp(row["added_at"]),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        msg = "Failed to parse conversation participant row"
        logger.warning(
            COS_GROUP_PARTICIPANT_FAILED,
            operation="deserialize",
            row_id=_safe_row_id(row),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def row_to_invite(row: RowLike) -> ConversationInvite:
    """Convert a database row into a :class:`ConversationInvite`.

    Returns:
        Result of type ``ConversationInvite``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        target_role = row["target_role"]
        return ConversationInvite(
            id=UUID(str(row["id"])),
            conversation_id=str(row["conversation_id"]),
            approval_id=str(row["approval_id"]),
            requested_by_agent_id=str(row["requested_by_agent_id"]),
            target_agent_id=str(row["target_agent_id"]),
            target_role=str(target_role) if target_role is not None else None,
            reason=str(row["reason"]),
            status=ConversationInviteStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        msg = "Failed to parse conversation invite row"
        logger.warning(
            COS_GROUP_INVITE_FAILED,
            operation="deserialize",
            row_id=_safe_row_id(row),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def row_to_proposal(row: RowLike) -> ConversationalProposal:
    """Convert a database row into a :class:`ConversationalProposal`.

    Shared by both backend repositories so the id column (a ``TEXT``
    column holding ``str(uuid)``) deserialises to a ``UUID`` identically
    on SQLite and Postgres, with one error path and one test surface.

    Returns:
        Result of type ``ConversationalProposal``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return ConversationalProposal(
            id=UUID(str(row["id"])),
            conversation_id=str(row["conversation_id"]),
            approval_id=str(row["approval_id"]),
            work_item_json=str(row["work_item_json"]),
            status=ConversationalProposalStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        msg = "Failed to parse conversational proposal row"
        logger.warning(
            PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
            operation="deserialize",
            row_id=_safe_row_id(row),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


__all__ = [
    "RowLike",
    "row_to_conversation",
    "row_to_invite",
    "row_to_participant",
    "row_to_proposal",
    "row_to_turn",
]
