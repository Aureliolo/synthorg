"""Tests for the conversation row-to-model marshalling helpers."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.meta.chief_of_staff.enums import ConversationParticipantStatus
from synthorg.persistence._conversation_marshalling import row_to_participant
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
