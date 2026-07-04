"""Unit tests for the typed turn-sequence conflict.

An exhausted ``(conversation_id, sequence)`` race must surface as a
retryable 409 ``TurnSequenceConflictError`` rather than the generic
non-retryable 400 the sqlstate-less ``ConstraintViolationError`` path
yields. Covers the error contract and the SQLite repo's exhaustion
branch driven by a fake connection that always collides on INSERT.
"""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest
from typeguard import suppress_type_checks

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.persistence_errors import (
    ConstraintViolationError,
    TurnSequenceConflictError,
)
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.persistence.sqlite.conversation_repo._turns import (
    SQLiteConversationTurnRepository,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_SEQUENCE_UNIQUE_MESSAGE = (
    "UNIQUE constraint failed: conversation_turns.conversation_id, "
    "conversation_turns.sequence"
)


class _FakeCursor:
    """aiosqlite-shaped cursor: awaitable for INSERT, async-CM for SELECT."""

    def __init__(self, sql: str) -> None:
        self._sql = sql

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _run() -> _FakeCursor:
            if "INSERT" in self._sql:
                raise sqlite3.IntegrityError(_SEQUENCE_UNIQUE_MESSAGE)
            return self

        return _run().__await__()

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetchone(self) -> tuple[int]:
        # Any taken sequence: the INSERT always collides regardless, so the
        # resequence loop cannot escape and must exhaust into the typed 409.
        return (5,)


class _AlwaysCollidingConn:
    """Fake aiosqlite connection whose every INSERT hits the sequence unique."""

    row_factory: object = None

    def execute(self, sql: str, _params: object = None) -> _FakeCursor:
        return _FakeCursor(sql)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@asynccontextmanager
async def _noop_write_context() -> AsyncIterator[None]:
    yield


def _make_turn() -> ConversationTurn:
    return ConversationTurn(
        id=as_uuid("turn-conflict"),
        conversation_id=sid("conv-conflict"),
        sequence=0,
        role=ConversationRole.USER,
        content="hello",
        author_agent_id=None,
        author_name=None,
        routed_topic=None,
        routing_confidence=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_turn_sequence_conflict_error_contract() -> None:
    assert issubclass(TurnSequenceConflictError, ConstraintViolationError)
    assert TurnSequenceConflictError.is_retryable is True
    assert TurnSequenceConflictError.status_code == 409
    assert TurnSequenceConflictError.error_category is ErrorCategory.CONFLICT
    assert TurnSequenceConflictError.error_code is ErrorCode.TURN_SEQUENCE_CONFLICT


async def test_exhausted_sequence_race_raises_typed_conflict() -> None:
    with suppress_type_checks():
        repo = SQLiteConversationTurnRepository(
            cast("aiosqlite.Connection", _AlwaysCollidingConn()),
            write_context=_noop_write_context,
        )
        with pytest.raises(TurnSequenceConflictError):
            await repo.append(_make_turn())
