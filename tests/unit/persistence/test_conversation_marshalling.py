"""Tests for the conversation row-to-model marshalling helpers."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conversation.enums import (
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationKind,
    ConversationParticipantStatus,
)
from synthorg.persistence._conversation_marshalling import (
    row_to_conversation,
    row_to_invite,
    row_to_participant,
    row_to_turn,
)
from tests._shared import as_uuid, sid

_NOW = datetime(2026, 5, 19, 9, 0, tzinfo=UTC)


def _participant_row(participant_id: str) -> dict[str, object]:
    """Build a DB row dict for ``row_to_participant`` with *participant_id*."""
    return {
        "id": participant_id,
        "conversation_id": "conv-1",
        "agent_id": "agent-1",
        "agent_name": "Dana",
        "participant_role": "CEO",
        "status": ConversationParticipantStatus.ACTIVE.value,
        "added_by": "user-1",
        "added_at": _NOW.isoformat(),
    }


@pytest.mark.unit
class TestRowToParticipant:
    """``row_to_participant`` deserialises the TEXT id column to a ``UUID``."""

    def test_valid_uuid_round_trips_to_uuid(self) -> None:
        participant = row_to_participant(_participant_row(sid("p1")))
        assert participant.id == as_uuid("p1")
        assert participant.conversation_id == "conv-1"
        assert participant.status is ConversationParticipantStatus.ACTIVE

    def test_malformed_id_raises_query_error(self) -> None:
        # A non-UUID value in the TEXT id column makes ``UUID(...)`` raise
        # ValueError, which the marshaller converts to a typed QueryError
        # rather than letting a corrupt id round-trip silently.
        with pytest.raises(QueryError):
            row_to_participant(_participant_row("not-a-uuid"))


def _conversation_row(conversation_id: str) -> dict[str, object]:
    """Build a DB row dict for ``row_to_conversation`` with *conversation_id*."""
    return {
        "id": conversation_id,
        "created_by": "user-1",
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
        "status": ConversationStatus.ACTIVE.value,
        "kind": ConversationKind.DIRECT.value,
    }


@pytest.mark.unit
class TestRowToConversation:
    """``row_to_conversation`` deserialises the TEXT id column to a ``UUID``."""

    def test_valid_uuid_round_trips_to_uuid(self) -> None:
        conversation = row_to_conversation(_conversation_row(sid("c1")))
        assert conversation.id == as_uuid("c1")
        assert conversation.created_by == "user-1"
        assert conversation.status is ConversationStatus.ACTIVE
        assert conversation.kind is ConversationKind.DIRECT

    def test_malformed_id_raises_query_error(self) -> None:
        with pytest.raises(QueryError):
            row_to_conversation(_conversation_row("not-a-uuid"))


def _turn_row(turn_id: str) -> dict[str, object]:
    """Build a DB row dict for ``row_to_turn`` with *turn_id*."""
    return {
        "id": turn_id,
        "conversation_id": "conv-1",
        "sequence": 0,
        "role": ConversationRole.USER.value,
        "content": "I need a landing page",
        "author_agent_id": None,
        "author_name": None,
        "routed_topic": None,
        "routing_confidence": None,
        "created_at": _NOW.isoformat(),
    }


@pytest.mark.unit
class TestRowToTurn:
    """``row_to_turn`` deserialises the TEXT id column to a ``UUID``."""

    def test_valid_uuid_round_trips_to_uuid(self) -> None:
        turn = row_to_turn(_turn_row(sid("t1")))
        assert turn.id == as_uuid("t1")
        assert turn.conversation_id == "conv-1"
        assert turn.sequence == 0
        assert turn.role is ConversationRole.USER

    def test_malformed_id_raises_query_error(self) -> None:
        with pytest.raises(QueryError):
            row_to_turn(_turn_row("not-a-uuid"))


def _invite_row(invite_id: str) -> dict[str, object]:
    """Build a DB row dict for ``row_to_invite`` with *invite_id*."""
    return {
        "id": invite_id,
        "conversation_id": "conv-1",
        "approval_id": "appr-1",
        "requested_by_agent_id": "agent-ceo",
        "target_agent_id": "agent-cfo",
        "target_role": "CFO",
        "reason": "budget sign-off needed",
        "status": ConversationInviteStatus.PENDING.value,
        "created_at": _NOW.isoformat(),
    }


@pytest.mark.unit
class TestRowToInvite:
    """``row_to_invite`` deserialises the TEXT id column to a ``UUID``."""

    def test_valid_uuid_round_trips_to_uuid(self) -> None:
        invite = row_to_invite(_invite_row(sid("i1")))
        assert invite.id == as_uuid("i1")
        assert invite.conversation_id == "conv-1"
        assert invite.approval_id == "appr-1"
        assert invite.status is ConversationInviteStatus.PENDING

    def test_malformed_id_raises_query_error(self) -> None:
        with pytest.raises(QueryError):
            row_to_invite(_invite_row("not-a-uuid"))
