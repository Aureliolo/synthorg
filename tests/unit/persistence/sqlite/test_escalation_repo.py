"""SQLite escalation queue repository tests."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import AwareDatetime

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationStatus,
    RejectDecision,
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
)
from synthorg.communication.enums import ConflictType
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.persistence.sqlite.escalation_repo import SQLiteEscalationRepository
from tests._shared import as_uuid
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit


def _make_conflict(conflict_id: str = "conflict-sql-0001") -> Conflict:
    """Build a two-agent conflict fixture."""
    return Conflict(
        id=as_uuid(conflict_id),
        type=ConflictType.ARCHITECTURE,
        subject="Pick a storage engine",
        positions=(
            ConflictPosition(
                agent_id="agent-a",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="PostgreSQL",
                reasoning="Strong consistency",
                timestamp=datetime.now(UTC),
            ),
            ConflictPosition(
                agent_id="agent-b",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="SQLite",
                reasoning="Simpler ops",
                timestamp=datetime.now(UTC),
            ),
        ),
        detected_at=datetime.now(UTC),
    )


def _make_escalation(
    *,
    escalation_id: str,
    expires_at: AwareDatetime | None = None,
    conflict_id: str | None = None,
) -> Escalation:
    """Build a pending escalation.

    ``conflict_id`` defaults to a value derived from ``escalation_id``
    so tests creating multiple escalations mirror production's
    "one PENDING escalation per conflict" invariant by default.
    """
    resolved_conflict_id = conflict_id or f"conflict-for-{escalation_id}"
    return Escalation(
        id=escalation_id,
        conflict=_make_conflict(resolved_conflict_id),
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )


@pytest.fixture
async def backend() -> AsyncIterator[SQLitePersistenceBackend]:
    """Connected in-memory SQLite backend with the escalations table."""
    be = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
    await be.connect()
    assert be._db is not None
    # Apply the escalations schema to the in-memory DB.
    await be._db.execute(
        """
        CREATE TABLE IF NOT EXISTS conflict_escalations (
            id TEXT NOT NULL PRIMARY KEY,
            conflict_id TEXT NOT NULL,
            conflict_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT,
            decided_at TEXT,
            decided_by TEXT,
            decision_json TEXT
        )
        """,
    )
    await be._db.commit()
    yield be
    await be.disconnect()


async def test_create_and_get(backend: SQLitePersistenceBackend) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    row = _make_escalation(escalation_id="escalation-sql-01")
    await repo.create(row)
    fetched = await repo.get("escalation-sql-01")
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.status == EscalationStatus.PENDING
    assert fetched.conflict.id == row.conflict.id


async def test_list_items_filters_by_status(backend: SQLitePersistenceBackend) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    await repo.create(_make_escalation(escalation_id="escalation-sql-a"))
    await repo.create(_make_escalation(escalation_id="escalation-sql-b"))
    pending, total_pending = await repo.list_items(
        status=EscalationStatus.PENDING,
    )
    assert total_pending == 2
    assert {e.id for e in pending} == {"escalation-sql-a", "escalation-sql-b"}
    decided, total_decided = await repo.list_items(
        status=EscalationStatus.DECIDED,
    )
    assert total_decided == 0
    assert decided == ()


async def test_apply_winner_decision_transitions_to_decided(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    await repo.create(_make_escalation(escalation_id="escalation-sql-win"))
    decision = WinnerDecision(
        winning_agent_id="agent-a",
        reasoning="Stronger consistency wins",
    )
    updated = await repo.apply_decision(
        "escalation-sql-win",
        decision=decision,
        decided_by="human:op-1",
    )
    assert updated.status == EscalationStatus.DECIDED
    assert updated.decision == decision
    assert updated.decided_by == "human:op-1"
    # Re-fetch from DB to confirm persistence
    reloaded = await repo.get("escalation-sql-win")
    assert reloaded is not None
    assert reloaded.status == EscalationStatus.DECIDED
    assert reloaded.decision == decision


async def test_apply_reject_decision_round_trips(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    await repo.create(_make_escalation(escalation_id="escalation-sql-rej"))
    decision = RejectDecision(reasoning="Both positions off-strategy")
    updated = await repo.apply_decision(
        "escalation-sql-rej",
        decision=decision,
        decided_by="human:op-2",
    )
    assert updated.status == EscalationStatus.DECIDED
    assert updated.decision == decision


async def test_apply_decision_on_missing_row_raises_keyerror(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    with pytest.raises(KeyError):
        await repo.apply_decision(
            "missing",
            decision=WinnerDecision(winning_agent_id="agent-a", reasoning="x"),
            decided_by="human:op-x",
        )


async def test_apply_decision_on_decided_row_raises_valueerror(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    await repo.create(_make_escalation(escalation_id="escalation-sql-dbl"))
    decision = WinnerDecision(winning_agent_id="agent-a", reasoning="ok")
    await repo.apply_decision(
        "escalation-sql-dbl",
        decision=decision,
        decided_by="human:op-1",
    )
    with pytest.raises(ValueError, match="cannot transition"):
        await repo.apply_decision(
            "escalation-sql-dbl",
            decision=decision,
            decided_by="human:op-2",
        )


async def test_cancel_transitions_to_cancelled(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    await repo.create(_make_escalation(escalation_id="escalation-sql-canc"))
    updated = await repo.cancel("escalation-sql-canc", cancelled_by="human:op-3")
    assert updated.status == EscalationStatus.CANCELLED
    assert updated.decided_by == "human:op-3"


async def test_mark_expired_transitions_stale_rows(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    past = datetime.now(UTC) - timedelta(seconds=30)
    future = datetime.now(UTC) + timedelta(seconds=3600)
    await repo.create(
        _make_escalation(escalation_id="escalation-sql-old", expires_at=past),
    )
    await repo.create(
        _make_escalation(escalation_id="escalation-sql-new", expires_at=future),
    )
    expired = await repo.mark_expired(datetime.now(UTC).isoformat())
    assert expired == ("escalation-sql-old",)
    old = await repo.get("escalation-sql-old")
    assert old is not None
    assert old.status == EscalationStatus.EXPIRED
    new = await repo.get("escalation-sql-new")
    assert new is not None
    assert new.status == EscalationStatus.PENDING


async def test_list_items_respects_limit_and_offset(
    backend: SQLitePersistenceBackend,
) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    for i in range(5):
        await repo.create(_make_escalation(escalation_id=f"escalation-sql-p-{i}"))
    page_a, total = await repo.list_items(limit=2, offset=0)
    assert total == 5
    assert len(page_a) == 2
    page_b, _ = await repo.list_items(limit=2, offset=2)
    assert len(page_b) == 2
    ids_a = {e.id for e in page_a}
    ids_b = {e.id for e in page_b}
    assert ids_a.isdisjoint(ids_b)


async def test_invalid_limit_raises(backend: SQLitePersistenceBackend) -> None:
    assert backend._db is not None
    repo = SQLiteEscalationRepository(
        backend._db, write_context=make_private_write_context()
    )
    with pytest.raises(ValueError, match="limit"):
        await repo.list_items(limit=0)
    with pytest.raises(ValueError, match="offset"):
        await repo.list_items(offset=-1)


async def test_build_escalations_serializes_with_backend_write_context(
    backend: SQLitePersistenceBackend,
) -> None:
    """``build_escalations`` must wire the backend's shared write lock.

    The factory previously constructed ``SQLiteEscalationRepository``
    without a shared lock, so escalation writes could interleave with
    sibling repos' writes on the single ``aiosqlite.Connection``. This
    regression test asserts that an escalation operation started while
    another task already holds ``backend.write_context()`` blocks until
    the holder releases, proving both sides contend on the same lock.
    """
    escalation_repo = backend.build_escalations()
    assert isinstance(escalation_repo, SQLiteEscalationRepository)

    events: list[str] = []
    backend_holding = asyncio.Event()
    release_backend = asyncio.Event()
    escalation_contender_started = asyncio.Event()

    async def hold_backend_lock() -> None:
        async with backend.write_context():
            events.append("backend-acquired")
            backend_holding.set()
            await release_backend.wait()
            events.append("backend-released")

    async def try_escalation_lock() -> None:
        await backend_holding.wait()
        escalation_contender_started.set()
        async with escalation_repo._write_context():
            events.append("escalation-acquired")

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(hold_backend_lock())
        _ = tg.create_task(try_escalation_lock())
        await escalation_contender_started.wait()
        for _ in range(5):
            await asyncio.sleep(0)
        assert "escalation-acquired" not in events, (
            f"escalation_repo acquired the lock while backend held it; events={events}"
        )
        release_backend.set()

    assert events == [
        "backend-acquired",
        "backend-released",
        "escalation-acquired",
    ], events
