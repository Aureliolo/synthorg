"""Conformance tests for the participant repository (SQLite + Postgres).

``ConversationParticipantRepository`` is not exposed on
``PersistenceBackend`` (the lifecycle wires it directly from the
connected backend), so this file builds the backend-specific concrete
repo over the migrated ``backend.get_db()`` handle. A parent
``conversations`` row is saved first to satisfy the FK. Both arms
exercise the same protocol surface so SQLite (TEXT timestamps) and
Postgres (TIMESTAMPTZ) divergence is caught by one assertion set.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import (
    ConversationKind,
    ConversationParticipantStatus,
)
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.meta.chief_of_staff.models import Conversation
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
    ConversationParticipantRepository,
)
from synthorg.persistence.conversation_protocol import ConversationRepository
from synthorg.persistence.postgres.conversation_participant_repo import (
    PostgresConversationParticipantRepository,
)
from synthorg.persistence.postgres.conversation_repo import (
    PostgresConversationRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.conversation_participant_repo import (
    SQLiteConversationParticipantRepository,
)
from synthorg.persistence.sqlite.conversation_repo import SQLiteConversationRepository
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _participant_repo(
    backend: PersistenceBackend,
) -> ConversationParticipantRepository:
    """Return a concrete participant repo bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteConversationParticipantRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresConversationParticipantRepository(
            cast("AsyncConnectionPool", handle)
        )
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


async def _save_conversation(backend: PersistenceBackend, conversation_id: str) -> None:
    """Persist a parent group conversation so the participant FK holds."""
    name = backend.backend_name
    handle = backend.get_db()
    repo: ConversationRepository
    if name == "sqlite":
        repo = SQLiteConversationRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    else:
        from psycopg_pool import AsyncConnectionPool

        repo = PostgresConversationRepository(cast("AsyncConnectionPool", handle))
    await repo.save(
        Conversation(
            id=as_uuid(conversation_id),
            created_by="user-001",
            created_at=_NOW,
            updated_at=_NOW,
            status=ConversationStatus.ACTIVE,
            kind=ConversationKind.GROUP,
        )
    )


def _make_participant(  # noqa: PLR0913 -- roster columns are optional kwargs
    *,
    participant_id: str = "part-001",
    conversation_id: str = "conv-grp",
    agent_id: str = "agent-cfo-001",
    agent_name: str = "Casey (CFO)",
    participant_role: str = "CFO",
    status: ConversationParticipantStatus = ConversationParticipantStatus.ACTIVE,
    added_by: str = "user-001",
    offset_seconds: int = 0,
    offset_micros: int = 0,
) -> ConversationParticipant:
    return ConversationParticipant(
        id=as_uuid(participant_id),
        conversation_id=sid(conversation_id),
        agent_id=agent_id,
        agent_name=agent_name,
        participant_role=participant_role,
        status=status,
        added_by=added_by,
        added_at=_NOW + timedelta(seconds=offset_seconds, microseconds=offset_micros),
    )


class TestConversationParticipantRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await _save_conversation(backend, "conv-grp")
        repo = _participant_repo(backend)
        participant = _make_participant()
        await repo.save(participant)

        fetched = await repo.get(NotBlankStr(str(participant.id)))
        assert fetched is not None
        assert fetched.id == participant.id
        assert fetched.agent_id == "agent-cfo-001"
        assert fetched.agent_name == "Casey (CFO)"
        assert fetched.participant_role == "CFO"
        assert fetched.status is ConversationParticipantStatus.ACTIVE
        assert fetched.added_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _participant_repo(backend)
        assert await repo.get(NotBlankStr("part-missing")) is None

    async def test_save_upsert_overwrites_status(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-grp")
        repo = _participant_repo(backend)
        participant = _make_participant(participant_id="part-upsert")
        await repo.save(participant)
        await repo.save(
            participant.model_copy(
                update={"status": ConversationParticipantStatus.REMOVED}
            )
        )
        fetched = await repo.get(NotBlankStr(str(participant.id)))
        assert fetched is not None
        assert fetched.status is ConversationParticipantStatus.REMOVED

    async def test_query_scopes_to_conversation(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-a")
        await _save_conversation(backend, "conv-b")
        repo = _participant_repo(backend)
        await repo.save(
            _make_participant(participant_id="pa", conversation_id="conv-a")
        )
        await repo.save(
            _make_participant(participant_id="pb", conversation_id="conv-b")
        )

        rows = await repo.query(
            ConversationParticipantFilterSpec(conversation_id=sid("conv-a"))
        )
        assert {r.id for r in rows} == {as_uuid("pa")}

    async def test_query_orders_by_added_at_microsecond_offsets(
        self, backend: PersistenceBackend
    ) -> None:
        # GroupChatService._enrol stamps added_at = now + index microseconds
        # to make round-robin order follow enrolment order. The whole-second
        # _NOW means "first" serialises WITHOUT a fraction and "second" WITH
        # one (".000001"), so this asserts the real SQLite TEXT lexicographic
        # sort handles the missing-fraction edge, not just an integer compare.
        await _save_conversation(backend, "conv-order")
        repo = _participant_repo(backend)
        await repo.save(
            _make_participant(
                participant_id="second",
                conversation_id="conv-order",
                agent_id="agent-ceo",
                offset_micros=1,
            )
        )
        await repo.save(
            _make_participant(
                participant_id="first",
                conversation_id="conv-order",
                agent_id="agent-cfo",
                offset_micros=0,
            )
        )
        rows = await repo.query(
            ConversationParticipantFilterSpec(conversation_id=sid("conv-order"))
        )
        assert [r.id for r in rows] == [as_uuid("first"), as_uuid("second")]

    async def test_query_filters_by_status(self, backend: PersistenceBackend) -> None:
        await _save_conversation(backend, "conv-status")
        repo = _participant_repo(backend)
        await repo.save(
            _make_participant(
                participant_id="active-one",
                conversation_id="conv-status",
                agent_id="agent-active",
            )
        )
        await repo.save(
            _make_participant(
                participant_id="removed-one",
                conversation_id="conv-status",
                agent_id="agent-removed",
                status=ConversationParticipantStatus.REMOVED,
            )
        )
        active = await repo.query(
            ConversationParticipantFilterSpec(
                conversation_id=sid("conv-status"),
                status=ConversationParticipantStatus.ACTIVE,
            )
        )
        assert {r.id for r in active} == {as_uuid("active-one")}

    async def test_count_matches_query(self, backend: PersistenceBackend) -> None:
        await _save_conversation(backend, "conv-count")
        repo = _participant_repo(backend)
        for index in range(3):
            await repo.save(
                _make_participant(
                    participant_id=f"c{index}",
                    conversation_id="conv-count",
                    agent_id=f"agent-{index}",
                )
            )
        count = await repo.count(
            ConversationParticipantFilterSpec(conversation_id=sid("conv-count"))
        )
        assert count == 3

    async def test_duplicate_conversation_agent_rejected(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-dup")
        repo = _participant_repo(backend)
        await repo.save(
            _make_participant(participant_id="d1", conversation_id="conv-dup")
        )
        # Same (conversation_id, agent_id) with a fresh row id breaches
        # the UNIQUE constraint -- the service uses transition_if to
        # re-activate, never a second save.
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_participant(participant_id="d2", conversation_id="conv-dup")
            )

    async def test_transition_if_flips_membership(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-trans")
        repo = _participant_repo(backend)
        participant = _make_participant(
            participant_id="t1", conversation_id="conv-trans"
        )
        await repo.save(participant)

        result = await repo.transition_if(
            NotBlankStr(str(participant.id)),
            from_state=ConversationParticipantStatus.ACTIVE,
            to_state=ConversationParticipantStatus.REMOVED,
        )
        assert result is True
        fetched = await repo.get(NotBlankStr(str(participant.id)))
        assert fetched is not None
        assert fetched.status is ConversationParticipantStatus.REMOVED

    async def test_transition_if_false_on_state_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-mismatch")
        repo = _participant_repo(backend)
        participant = _make_participant(
            participant_id="m1",
            conversation_id="conv-mismatch",
            status=ConversationParticipantStatus.REMOVED,
        )
        await repo.save(participant)
        result = await repo.transition_if(
            NotBlankStr(str(participant.id)),
            from_state=ConversationParticipantStatus.ACTIVE,
            to_state=ConversationParticipantStatus.REMOVED,
        )
        assert result is False

    async def test_transition_if_false_on_missing_row(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _participant_repo(backend)
        result = await repo.transition_if(
            NotBlankStr("part-none"),
            from_state=ConversationParticipantStatus.ACTIVE,
            to_state=ConversationParticipantStatus.REMOVED,
        )
        assert result is False

    async def test_transition_if_rejects_update_keys(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-keys")
        repo = _participant_repo(backend)
        participant = _make_participant(
            participant_id="k1", conversation_id="conv-keys"
        )
        await repo.save(participant)
        with pytest.raises(QueryError):
            await repo.transition_if(
                NotBlankStr(str(participant.id)),
                from_state=ConversationParticipantStatus.ACTIVE,
                to_state=ConversationParticipantStatus.REMOVED,
                added_by="someone",
            )

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        await _save_conversation(backend, "conv-del")
        repo = _participant_repo(backend)
        participant = _make_participant(
            participant_id="del1", conversation_id="conv-del"
        )
        await repo.save(participant)
        assert await repo.delete(NotBlankStr(str(participant.id))) is True
        assert await repo.get(NotBlankStr(str(participant.id))) is None
        assert await repo.delete(NotBlankStr(str(participant.id))) is False

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        repo = _participant_repo(backend)
        assert isinstance(repo, ConversationParticipantRepository)
