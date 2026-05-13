"""Tests for SQLiteDecisionRepository."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from synthorg.core.enums import DecisionOutcome
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.engine.decisions import DecisionRecord
from synthorg.persistence.decision_protocol import DecisionRepository
from synthorg.persistence.sqlite.decision_repo import SQLiteDecisionRepository
from tests._shared.persistence import make_private_write_context

if TYPE_CHECKING:
    import aiosqlite


async def _append(  # noqa: PLR0913
    repo: SQLiteDecisionRepository,
    *,
    record_id: str | None = None,
    task_id: str = "task-1",
    approval_id: str | None = None,
    executing_agent_id: str = "alice",
    reviewer_agent_id: str = "bob",
    decision: DecisionOutcome = DecisionOutcome.APPROVED,
    reason: str | None = None,
    criteria_snapshot: tuple[str, ...] = (),
    recorded_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> DecisionRecord:
    """Append a record via the repository with sensible defaults."""
    return await repo.append_with_next_version(
        record_id=record_id or str(uuid4()),
        task_id=task_id,
        approval_id=approval_id,
        executing_agent_id=executing_agent_id,
        reviewer_agent_id=reviewer_agent_id,
        decision=decision,
        reason=reason,
        criteria_snapshot=criteria_snapshot,
        recorded_at=recorded_at or datetime.now(UTC),
        metadata=metadata if metadata is not None else {},
    )


@pytest.mark.unit
class TestSQLiteDecisionRepositoryAppendAndGet:
    async def test_append_and_get(self, migrated_db: aiosqlite.Connection) -> None:
        """Append a record, retrieve by ID, fields match."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        record = await _append(
            repo,
            record_id="dr-001",
            criteria_snapshot=("JWT login", "Refresh works"),
            metadata={"sprint": "5"},
            reason="Code quality is high",
        )
        assert record.version == 1

        fetched = await repo.get("dr-001")
        assert fetched is not None
        assert fetched.id == "dr-001"
        assert fetched.task_id == "task-1"
        assert fetched.executing_agent_id == "alice"
        assert fetched.reviewer_agent_id == "bob"
        assert fetched.decision is DecisionOutcome.APPROVED
        assert fetched.reason == "Code quality is high"
        assert fetched.criteria_snapshot == ("JWT login", "Refresh works")
        assert fetched.metadata == {"sprint": "5"}
        assert fetched.version == 1

    async def test_get_missing_returns_none(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """get returns None for unknown ID."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        assert await repo.get("nonexistent") is None

    async def test_append_duplicate_id_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Appending with an existing ID raises DuplicateRecordError."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await _append(repo, record_id="dr-dup")
        with pytest.raises(DuplicateRecordError):
            await _append(repo, record_id="dr-dup", task_id="task-2")

    async def test_append_computes_monotonic_version(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Multiple appends on the same task yield versions 1, 2, 3."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        r1 = await _append(repo, record_id="dr-a", task_id="task-1")
        r2 = await _append(
            repo,
            record_id="dr-b",
            task_id="task-1",
            reviewer_agent_id="carol",
        )
        r3 = await _append(
            repo,
            record_id="dr-c",
            task_id="task-1",
            reviewer_agent_id="dave",
        )
        assert r1.version == 1
        assert r2.version == 2
        assert r3.version == 3

    async def test_append_versions_are_per_task(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Version counters are independent per task_id."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        a1 = await _append(repo, record_id="dr-a1", task_id="task-A")
        b1 = await _append(repo, record_id="dr-b1", task_id="task-B")
        a2 = await _append(
            repo,
            record_id="dr-a2",
            task_id="task-A",
            reviewer_agent_id="carol",
        )
        assert a1.version == 1
        assert b1.version == 1
        assert a2.version == 2


@pytest.mark.unit
class TestSQLiteDecisionRepositoryListByTask:
    async def test_list_by_task_returns_oldest_first_with_backfill(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """list_by_task returns records ordered (recorded_at ASC, id ASC).

        Pins the protocol's chronological-ordering contract by inserting
        a backfilled decision LAST in real time but with the EARLIEST
        ``recorded_at``: under the previous ``ORDER BY version`` shape,
        the backfill would sort to position 3 (highest version); under
        the new ``ORDER BY recorded_at ASC, id ASC`` shape, it sorts to
        position 1. The version-list assertion ``[3, 1, 2]`` therefore
        fails the old query and passes the new one, which is the
        regression the test exists to catch.
        """
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        anchor = datetime(2026, 1, 1, tzinfo=UTC)
        # Two regular decisions, recorded_at increasing in line with
        # version assignment (the normal happy path).
        await _append(
            repo,
            record_id="dr-1",
            task_id="task-A",
            recorded_at=anchor + timedelta(seconds=10),
        )
        await _append(
            repo,
            record_id="dr-2",
            task_id="task-A",
            reviewer_agent_id="carol",
            recorded_at=anchor + timedelta(seconds=20),
        )
        # Backfill: inserted LAST so it gets ``version=3``, but its
        # ``recorded_at`` is BEFORE the others.
        await _append(
            repo,
            record_id="dr-backfill",
            task_id="task-A",
            reviewer_agent_id="dave",
            recorded_at=anchor,
        )
        # Unrelated record under a different task to confirm task
        # filter is independent of the ordering change.
        await _append(repo, record_id="dr-4", task_id="task-B")

        results = await repo.list_by_task("task-A")
        assert len(results) == 3
        # Chronological order must place the backfill first even
        # though it has the highest version.
        assert [r.id for r in results] == ["dr-backfill", "dr-1", "dr-2"]
        assert [r.version for r in results] == [3, 1, 2]

    async def test_list_by_task_empty(self, migrated_db: aiosqlite.Connection) -> None:
        """list_by_task returns empty tuple for unknown task."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        assert await repo.list_by_task("task-nope") == ()


@pytest.mark.unit
class TestSQLiteDecisionRepositoryListByAgent:
    async def test_list_by_agent_as_executor(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """list_by_agent with role='executor' filters by executing_agent_id.

        Uses two matching records to also verify DESC recorded_at ordering.
        """
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        now = datetime.now(UTC)
        await _append(
            repo,
            record_id="dr-1",
            executing_agent_id="alice",
            reviewer_agent_id="bob",
            recorded_at=now,
        )
        await _append(
            repo,
            record_id="dr-2",
            task_id="task-2",
            executing_agent_id="alice",
            reviewer_agent_id="carol",
            recorded_at=now + timedelta(seconds=1),
        )
        await _append(
            repo,
            record_id="dr-3",
            task_id="task-3",
            executing_agent_id="dave",
            reviewer_agent_id="alice",
            recorded_at=now + timedelta(seconds=2),
        )
        results = await repo.list_by_agent("alice", role="executor")
        assert len(results) == 2
        # DESC by recorded_at
        assert results[0].id == "dr-2"
        assert results[1].id == "dr-1"

    async def test_list_by_agent_as_reviewer(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """list_by_agent with role='reviewer' filters by reviewer_agent_id."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        now = datetime.now(UTC)
        await _append(
            repo,
            record_id="dr-1",
            executing_agent_id="alice",
            reviewer_agent_id="bob",
            recorded_at=now,
        )
        await _append(
            repo,
            record_id="dr-2",
            task_id="task-2",
            executing_agent_id="carol",
            reviewer_agent_id="bob",
            recorded_at=now + timedelta(seconds=1),
        )
        results = await repo.list_by_agent("bob", role="reviewer")
        assert len(results) == 2
        # DESC by recorded_at
        assert results[0].id == "dr-2"
        assert results[1].id == "dr-1"

    async def test_list_by_agent_invalid_role_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Invalid role raises QueryError (matches pagination-arg behaviour)."""
        from synthorg.core.persistence_errors import QueryError

        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        with pytest.raises(QueryError, match="role must be"):
            await repo.list_by_agent("alice", role="observer")  # type: ignore[arg-type]


@pytest.mark.unit
class TestSQLiteDecisionRepositoryProtocol:
    def test_satisfies_protocol(self, migrated_db: aiosqlite.Connection) -> None:
        """SQLiteDecisionRepository is a DecisionRepository."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        assert isinstance(repo, DecisionRepository)


@pytest.mark.unit
class TestSQLiteDecisionRepositorySerialization:
    async def test_criteria_and_metadata_round_trip(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """criteria_snapshot and metadata survive JSON round-trip.

        Metadata is exposed as a recursively frozen read-only view
        (``MappingProxyType`` at every mapping level) to preserve the
        append-only contract; this test compares via ``dict(...)``
        to avoid depending on the exact container type.
        """
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await _append(
            repo,
            record_id="dr-1",
            criteria_snapshot=("a", "b", "c"),
            metadata={"key": "value", "nested": {"x": 1}},
        )
        fetched = await repo.get("dr-1")
        assert fetched is not None
        assert fetched.criteria_snapshot == ("a", "b", "c")
        assert fetched.metadata["key"] == "value"
        assert dict(fetched.metadata["nested"]) == {"x": 1}

    async def test_corrupted_criteria_raises_query_error(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Corrupted criteria_snapshot JSON raises QueryError on read."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await _append(repo, record_id="dr-1")
        await migrated_db.execute(
            "UPDATE decision_records SET criteria_snapshot = ? WHERE id = ?",
            ("{not-valid-json}", "dr-1"),
        )
        await migrated_db.commit()
        with pytest.raises(QueryError):
            await repo.get("dr-1")

    async def test_corrupted_metadata_raises_query_error(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Corrupted metadata JSON raises QueryError on read."""
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await _append(repo, record_id="dr-1")
        await migrated_db.execute(
            "UPDATE decision_records SET metadata = ? WHERE id = ?",
            ("{not-valid-json}", "dr-1"),
        )
        await migrated_db.commit()
        with pytest.raises(QueryError):
            await repo.get("dr-1")

    async def test_schema_check_rejects_bogus_decision_value(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """DB-level CHECK constraint fires on UPDATE with unknown enum value.

        The CHECK(decision IN (...)) constraint is the write-time
        guard against schema drift -- an invalid decision value can
        never reach the DB in the first place.
        """
        import sqlite3

        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await _append(repo, record_id="dr-1")

        async def _attempt_bogus_update() -> None:
            await migrated_db.execute(
                "UPDATE decision_records SET decision = 'bogus' WHERE id = ?",
                ("dr-1",),
            )
            await migrated_db.commit()

        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            await _attempt_bogus_update()
        assert exc_info.value.sqlite_errorname == "SQLITE_CONSTRAINT_CHECK"

    async def test_read_time_guard_wraps_deserialization_errors(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """Corrupted rows surface as QueryError on read.

        Uses `PRAGMA ignore_check_constraints = ON` to bypass the
        write-time CHECK and inject a bogus decision value, then
        verifies the read-time guard wraps the resulting
        ValidationError as QueryError.
        """
        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await _append(repo, record_id="dr-1")
        # Disable CHECK constraints only for this direct UPDATE so we
        # can inject an invalid enum value and exercise the read-time
        # deserialization guard.
        await migrated_db.execute("PRAGMA ignore_check_constraints = ON")
        try:
            await migrated_db.execute(
                "UPDATE decision_records SET decision = 'bogus' WHERE id = ?",
                ("dr-1",),
            )
            await migrated_db.commit()
        finally:
            await migrated_db.execute("PRAGMA ignore_check_constraints = OFF")
        with pytest.raises(QueryError):
            await repo.get("dr-1")

    async def test_concurrent_appends_yield_distinct_versions(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """Regression for the TOCTOU-safe version contract.

        Fires many append_with_next_version calls concurrently via
        asyncio.gather and asserts every call receives a distinct
        monotonic version with no UNIQUE(task_id, version)
        collisions.  This pins the invariant that justifies the
        atomic ``COALESCE(MAX(version), 0) + 1`` subquery inside the
        INSERT statement.
        """
        import asyncio

        repo = SQLiteDecisionRepository(
            migrated_db, write_context=make_private_write_context()
        )
        writer_count = 20

        async def _one(i: int) -> int:
            record = await _append(
                repo,
                record_id=f"dr-{i}",
                task_id="task-concurrent",
                reviewer_agent_id=f"reviewer-{i}",
            )
            return record.version

        versions = await asyncio.gather(
            *(_one(i) for i in range(writer_count)),
        )
        assert sorted(versions) == list(range(1, writer_count + 1))
        stored = await repo.list_by_task("task-concurrent")
        assert [r.version for r in stored] == list(range(1, writer_count + 1))


@pytest.mark.unit
class TestDecisionRecordSelfReviewInvariant:
    async def test_self_review_rejected_at_db_check_constraint(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """A direct INSERT with executor == reviewer is rejected by the DB CHECK.

        Defense-in-depth: the ``DecisionRecord`` Pydantic model
        rejects self-review at construction time, so the service path
        (``append_with_next_version``) never reaches the SQL layer
        with a self-review row.  This test bypasses the Pydantic
        validator via direct SQL to exercise the schema-level
        ``CHECK(reviewer_agent_id != executing_agent_id)`` constraint
        explicitly -- guarding against any future code path (raw
        SQL, migrations, third-party backends) that might bypass the
        model layer.
        """
        import sqlite3

        async def _insert_self_review() -> None:
            await migrated_db.execute(
                "INSERT INTO decision_records (id, task_id, "
                "executing_agent_id, reviewer_agent_id, decision, "
                "criteria_snapshot, recorded_at, version, metadata) "
                "VALUES (?, ?, ?, ?, 'approved', '[]', "
                "'2026-04-04T12:00:00+00:00', 1, '{}')",
                ("dr-self", "task-1", "alice", "alice"),
            )
            await migrated_db.commit()

        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            await _insert_self_review()
        assert exc_info.value.sqlite_errorname == "SQLITE_CONSTRAINT_CHECK"

    def test_self_review_rejected_at_pydantic_model(self) -> None:
        """``DecisionRecord`` model validator also rejects self-review.

        Callers that construct records directly (tests, migrations,
        future code paths) hit the Pydantic validator before reaching
        the database, so the invariant is enforced at both layers.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="executing_agent_id"):
            DecisionRecord(
                id="dr-self",
                task_id="task-1",
                executing_agent_id="alice",
                reviewer_agent_id="alice",
                decision=DecisionOutcome.APPROVED,
                recorded_at=datetime.now(UTC),
                version=1,
            )
