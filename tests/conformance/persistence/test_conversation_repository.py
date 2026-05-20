"""Conformance tests for the conversation repositories (SQLite + Postgres).

``ConversationRepository`` / ``ConversationTurnRepository`` are not
exposed on ``PersistenceBackend`` (the lifecycle wires them directly
from the connected backend), so this file builds the backend-specific
concrete repos over the migrated ``backend.get_db()`` handle. Both
arms exercise the same protocol surface so SQLite (TEXT timestamps,
INTEGER sequence) and Postgres (TIMESTAMPTZ, INTEGER) divergence is
caught by one assertion set.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnFilterSpec,
    ConversationTurnRepository,
)
from synthorg.persistence.postgres.conversation_repo import (
    PostgresConversationRepository,
    PostgresConversationTurnRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.conversation_repo import (
    SQLiteConversationRepository,
    SQLiteConversationTurnRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _conversation_repo(backend: PersistenceBackend) -> ConversationRepository:
    """Return a concrete ``ConversationRepository`` bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteConversationRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresConversationRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _turn_repo(backend: PersistenceBackend) -> ConversationTurnRepository:
    """Return a concrete ``ConversationTurnRepository`` bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteConversationTurnRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresConversationTurnRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make_conversation(
    *,
    conversation_id: str = "conv-001",
    status: ConversationStatus = ConversationStatus.ACTIVE,
) -> Conversation:
    return Conversation(
        id=conversation_id,
        created_by="user-001",
        created_at=_NOW,
        updated_at=_NOW,
        status=status,
    )


def _make_turn(
    *,
    turn_id: str = "turn-001",
    conversation_id: str = "conv-001",
    sequence: int = 0,
    role: ConversationRole = ConversationRole.USER,
    content: str = "I need a landing page",
) -> ConversationTurn:
    return ConversationTurn(
        id=turn_id,
        conversation_id=conversation_id,
        sequence=sequence,
        role=role,
        content=content,
        created_at=_NOW + timedelta(seconds=sequence),
    )


class TestConversationRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _conversation_repo(backend)
        conv = _make_conversation()
        await repo.save(conv)

        fetched = await repo.get(conv.id)
        assert fetched is not None
        assert fetched.id == conv.id
        assert fetched.created_by == "user-001"
        assert fetched.status is ConversationStatus.ACTIVE
        assert fetched.created_at.tzinfo is not None
        assert fetched.updated_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _conversation_repo(backend)
        assert await repo.get("conv-missing") is None

    async def test_save_commits_visible_to_fresh_repo(
        self, backend: PersistenceBackend
    ) -> None:
        first = _conversation_repo(backend)
        conv = _make_conversation(conversation_id="conv-commit")
        await first.save(conv)

        second = _conversation_repo(backend)
        fetched = await second.get(conv.id)
        assert fetched is not None
        assert fetched.id == conv.id

    async def test_save_upsert_overwrites_status(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _conversation_repo(backend)
        conv = _make_conversation(conversation_id="conv-upsert")
        await repo.save(conv)
        await repo.save(conv.model_copy(update={"status": ConversationStatus.PROPOSED}))

        fetched = await repo.get(conv.id)
        assert fetched is not None
        assert fetched.status is ConversationStatus.PROPOSED

    async def test_list_items_returns_saved(self, backend: PersistenceBackend) -> None:
        repo = _conversation_repo(backend)
        await repo.save(_make_conversation(conversation_id="a"))
        await repo.save(_make_conversation(conversation_id="b"))

        ids = {c.id for c in await repo.list_items()}
        assert {"a", "b"} <= ids

    async def test_transition_if_flips_state_atomically(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _conversation_repo(backend)
        conv = _make_conversation(conversation_id="conv-trans")
        await repo.save(conv)

        later = (_NOW + timedelta(minutes=5)).isoformat()
        result = await repo.transition_if(
            conv.id,
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.PROPOSED,
            updated_at=later,
        )
        assert result is True

        fetched = await repo.get(conv.id)
        assert fetched is not None
        assert fetched.status is ConversationStatus.PROPOSED

    async def test_transition_if_returns_false_on_state_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _conversation_repo(backend)
        conv = _make_conversation(
            conversation_id="conv-mismatch",
            status=ConversationStatus.CLOSED,
        )
        await repo.save(conv)

        result = await repo.transition_if(
            conv.id,
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.PROPOSED,
        )
        assert result is False
        fetched = await repo.get(conv.id)
        assert fetched is not None
        assert fetched.status is ConversationStatus.CLOSED

    async def test_transition_if_returns_false_on_missing_row(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _conversation_repo(backend)
        result = await repo.transition_if(
            NotBlankStr("conv-none"),
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.PROPOSED,
        )
        assert result is False

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _conversation_repo(backend)
        conv = _make_conversation(conversation_id="conv-del")
        await repo.save(conv)
        assert await repo.delete(conv.id) is True
        assert await repo.get(conv.id) is None
        assert await repo.delete(conv.id) is False

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        repo = _conversation_repo(backend)
        assert isinstance(repo, ConversationRepository)


class TestConversationTurnRepository:
    async def test_append_and_query(self, backend: PersistenceBackend) -> None:
        conv_repo = _conversation_repo(backend)
        await conv_repo.save(_make_conversation(conversation_id="conv-turns"))
        repo = _turn_repo(backend)
        await repo.append(
            _make_turn(turn_id="t0", conversation_id="conv-turns", sequence=0)
        )
        await repo.append(
            _make_turn(
                turn_id="t1",
                conversation_id="conv-turns",
                sequence=1,
                role=ConversationRole.ASSISTANT,
                content="Which audience?",
            )
        )

        rows = await repo.query(
            ConversationTurnFilterSpec(conversation_id=NotBlankStr("conv-turns"))
        )
        # Append-only invariant: newest-first (sequence DESC).
        assert [r.sequence for r in rows] == [1, 0]
        assert rows[0].role is ConversationRole.ASSISTANT

    async def test_append_duplicate_sequence_resequenced(
        self, backend: PersistenceBackend
    ) -> None:
        # Race-safe append: when a caller passes a ``sequence`` that
        # collides on the ``(conversation_id, sequence)`` uniqueness
        # constraint, the repo re-queries the live max sequence and
        # retries the insert. This is the TOCTOU defence for two
        # concurrent ``converse()`` calls computing the same sequence
        # from a stale snapshot; the second call lands at the next
        # available sequence rather than 5xx-ing the request.
        conv_repo = _conversation_repo(backend)
        await conv_repo.save(_make_conversation(conversation_id="conv-dup"))
        repo = _turn_repo(backend)
        await repo.append(
            _make_turn(turn_id="d0", conversation_id="conv-dup", sequence=0)
        )
        await repo.append(
            _make_turn(
                turn_id="d0-again",
                conversation_id="conv-dup",
                sequence=0,
            )
        )
        rows = await repo.query(
            ConversationTurnFilterSpec(conversation_id=NotBlankStr("conv-dup"))
        )
        # Newest-first ordering; both rows land with distinct
        # sequences (the second was resequenced to 1).
        sequences = sorted(t.sequence for t in rows)
        assert sequences == [0, 1]
        ids = {t.id for t in rows}
        assert ids == {"d0", "d0-again"}

    async def test_query_scopes_to_conversation(
        self, backend: PersistenceBackend
    ) -> None:
        conv_repo = _conversation_repo(backend)
        await conv_repo.save(_make_conversation(conversation_id="c-a"))
        await conv_repo.save(_make_conversation(conversation_id="c-b"))
        repo = _turn_repo(backend)
        await repo.append(_make_turn(turn_id="qa", conversation_id="c-a", sequence=0))
        await repo.append(_make_turn(turn_id="qb", conversation_id="c-b", sequence=0))

        rows = await repo.query(
            ConversationTurnFilterSpec(conversation_id=NotBlankStr("c-a"))
        )
        assert {r.id for r in rows} == {"qa"}

    async def test_purge_before_removes_old_turns(
        self, backend: PersistenceBackend
    ) -> None:
        conv_repo = _conversation_repo(backend)
        await conv_repo.save(_make_conversation(conversation_id="c-purge"))
        repo = _turn_repo(backend)
        await repo.append(
            _make_turn(turn_id="old", conversation_id="c-purge", sequence=0)
        )
        removed = await repo.purge_before(_NOW + timedelta(hours=1))
        assert removed >= 1
        rows = await repo.query(
            ConversationTurnFilterSpec(conversation_id=NotBlankStr("c-purge"))
        )
        assert rows == ()

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        repo = _turn_repo(backend)
        assert isinstance(repo, ConversationTurnRepository)
