"""Tests for SQLiteAuditRepository."""

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.persistence.errors import DuplicateRecordError, QueryError
from synthorg.persistence.repositories import AuditRepository
from synthorg.persistence.sqlite.audit_repository import (
    SQLiteAuditRepository,
)
from synthorg.security.models import AuditEntry, AuditVerdictStr

if TYPE_CHECKING:
    import aiosqlite

_DUMMY_HASH = "a" * 64


def _make_entry(  # noqa: PLR0913
    *,
    entry_id: str | None = None,
    timestamp: datetime | None = None,
    agent_id: str | None = "agent-001",
    task_id: str | None = "task-001",
    tool_name: str = "shell_exec",
    tool_category: ToolCategory = ToolCategory.CODE_EXECUTION,
    action_type: str = "code:write",
    arguments_hash: str = _DUMMY_HASH,
    verdict: AuditVerdictStr = "allow",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.LOW,
    reason: str = "Allowed by default policy",
    matched_rules: tuple[str, ...] = (),
    evaluation_duration_ms: float = 1.5,
    approval_id: str | None = None,
) -> AuditEntry:
    return AuditEntry(
        id=entry_id or str(uuid4()),
        timestamp=timestamp or datetime.now(UTC),
        agent_id=agent_id,
        task_id=task_id,
        tool_name=tool_name,
        tool_category=tool_category,
        action_type=action_type,
        arguments_hash=arguments_hash,
        verdict=verdict,
        risk_level=risk_level,
        reason=reason,
        matched_rules=matched_rules,
        evaluation_duration_ms=evaluation_duration_ms,
        approval_id=approval_id,
    )


@pytest.mark.unit
class TestSQLiteAuditRepository:
    async def test_save_and_query_all(self, migrated_db: aiosqlite.Connection) -> None:
        """Save 2 entries, query without filters, verify both returned."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(entry_id="ae-001")
        e2 = _make_entry(entry_id="ae-002")
        await repo.save(e1)
        await repo.save(e2)

        results = await repo.query()
        assert len(results) == 2
        ids = {r.id for r in results}
        assert "ae-001" in ids
        assert "ae-002" in ids

    async def test_query_returns_newest_first(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Entries with different timestamps are returned DESC order."""
        repo = SQLiteAuditRepository(migrated_db)
        now = datetime.now(UTC)
        earlier = now - timedelta(hours=1)

        e_old = _make_entry(entry_id="ae-old", timestamp=earlier)
        e_new = _make_entry(entry_id="ae-new", timestamp=now)
        # Save in chronological order to ensure DB ordering is not insertion order.
        await repo.save(e_old)
        await repo.save(e_new)

        results = await repo.query()
        assert len(results) == 2
        assert results[0].id == "ae-new"
        assert results[1].id == "ae-old"

    async def test_query_filter_by_agent_id(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Filter matches only entries with matching agent_id."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(entry_id="ae-a1", agent_id="agent-a")
        e2 = _make_entry(entry_id="ae-b1", agent_id="agent-b")
        await repo.save(e1)
        await repo.save(e2)

        results = await repo.query(agent_id="agent-a")
        assert len(results) == 1
        assert results[0].id == "ae-a1"

    async def test_query_filter_by_action_type(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Filter by action_type string."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(entry_id="ae-cw", action_type="code:write")
        e2 = _make_entry(entry_id="ae-cr", action_type="code:read")
        await repo.save(e1)
        await repo.save(e2)

        results = await repo.query(action_type="code:write")
        assert len(results) == 1
        assert results[0].id == "ae-cw"

    async def test_query_filter_by_verdict(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Filter by verdict (allow/deny/escalate/output_scan)."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(entry_id="ae-allow", verdict="allow")
        e2 = _make_entry(entry_id="ae-deny", verdict="deny")
        await repo.save(e1)
        await repo.save(e2)

        results = await repo.query(verdict="deny")
        assert len(results) == 1
        assert results[0].id == "ae-deny"

    async def test_query_filter_by_risk_level(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Filter by ApprovalRiskLevel enum."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(entry_id="ae-low", risk_level=ApprovalRiskLevel.LOW)
        e2 = _make_entry(entry_id="ae-high", risk_level=ApprovalRiskLevel.HIGH)
        await repo.save(e1)
        await repo.save(e2)

        results = await repo.query(risk_level=ApprovalRiskLevel.HIGH)
        assert len(results) == 1
        assert results[0].id == "ae-high"

    async def test_query_filter_by_since(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Entries before cutoff are excluded."""
        repo = SQLiteAuditRepository(migrated_db)
        now = datetime.now(UTC)
        old = now - timedelta(hours=2)
        cutoff = now - timedelta(hours=1)

        e_old = _make_entry(entry_id="ae-old", timestamp=old)
        e_new = _make_entry(entry_id="ae-new", timestamp=now)
        await repo.save(e_old)
        await repo.save(e_new)

        results = await repo.query(since=cutoff)
        assert len(results) == 1
        assert results[0].id == "ae-new"

    async def test_query_combined_filters(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Multiple filters are AND-combined."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(
            entry_id="ae-match",
            agent_id="agent-x",
            verdict="deny",
        )
        e2 = _make_entry(
            entry_id="ae-wrong-agent",
            agent_id="agent-y",
            verdict="deny",
        )
        e3 = _make_entry(
            entry_id="ae-wrong-verdict",
            agent_id="agent-x",
            verdict="allow",
        )
        await repo.save(e1)
        await repo.save(e2)
        await repo.save(e3)

        results = await repo.query(agent_id="agent-x", verdict="deny")
        assert len(results) == 1
        assert results[0].id == "ae-match"

    async def test_query_limit(self, migrated_db: aiosqlite.Connection) -> None:
        """Limit returns the N newest entries."""
        repo = SQLiteAuditRepository(migrated_db)
        base = datetime(2026, 3, 1, tzinfo=UTC)
        for i in range(5):
            await repo.save(
                _make_entry(
                    entry_id=f"ae-{i}",
                    timestamp=base + timedelta(minutes=i),
                )
            )

        results = await repo.query(limit=2)
        assert [e.id for e in results] == ["ae-4", "ae-3"]

    async def test_query_empty(self, migrated_db: aiosqlite.Connection) -> None:
        """Returns empty tuple when no entries."""
        repo = SQLiteAuditRepository(migrated_db)
        results = await repo.query()
        assert results == ()

    async def test_query_invalid_limit_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """limit < 1 raises QueryError."""
        repo = SQLiteAuditRepository(migrated_db)
        with pytest.raises(QueryError, match="limit"):
            await repo.query(limit=0)
        with pytest.raises(QueryError, match="limit"):
            await repo.query(limit=-1)

    async def test_round_trip_all_fields(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Full model round-trip including optional fields."""
        repo = SQLiteAuditRepository(migrated_db)
        entry = _make_entry(
            entry_id="ae-rt",
            agent_id="agent-rt",
            task_id="task-rt",
            tool_name="git_commit",
            tool_category=ToolCategory.VERSION_CONTROL,
            action_type="vcs:commit",
            verdict="escalate",
            risk_level=ApprovalRiskLevel.CRITICAL,
            reason="High-risk action needs approval",
            matched_rules=("no-force-push", "require-review"),
            evaluation_duration_ms=42.5,
            approval_id="approval-123",
        )
        await repo.save(entry)

        results = await repo.query()
        assert len(results) == 1
        result = results[0]
        assert result.id == entry.id
        assert result.timestamp == entry.timestamp
        assert result.agent_id == entry.agent_id
        assert result.task_id == entry.task_id
        assert result.tool_name == entry.tool_name
        assert result.tool_category == entry.tool_category
        assert result.action_type == entry.action_type
        assert result.arguments_hash == entry.arguments_hash
        assert result.verdict == entry.verdict
        assert result.risk_level == entry.risk_level
        assert result.reason == entry.reason
        assert result.matched_rules == entry.matched_rules
        assert result.evaluation_duration_ms == entry.evaluation_duration_ms
        assert result.approval_id == entry.approval_id

    async def test_round_trip_null_optional_fields(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """None for agent_id, task_id, approval_id survives round-trip."""
        repo = SQLiteAuditRepository(migrated_db)
        entry = _make_entry(
            entry_id="ae-null",
            agent_id=None,
            task_id=None,
            approval_id=None,
        )
        await repo.save(entry)

        results = await repo.query()
        assert len(results) == 1
        result = results[0]
        assert result.agent_id is None
        assert result.task_id is None
        assert result.approval_id is None

    async def test_round_trip_matched_rules(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Tuple of rules survives JSON serialization round-trip."""
        repo = SQLiteAuditRepository(migrated_db)
        rules = ("rule-alpha", "rule-beta", "rule-gamma")
        entry = _make_entry(entry_id="ae-rules", matched_rules=rules)
        await repo.save(entry)

        results = await repo.query()
        assert len(results) == 1
        assert results[0].matched_rules == rules

    async def test_deserialize_corrupt_data_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Corrupt matched_rules JSON triggers QueryError."""
        await migrated_db.execute(
            "INSERT INTO audit_entries ("
            "  id, timestamp, agent_id, task_id, tool_name, tool_category,"
            "  action_type, arguments_hash, verdict, risk_level, reason,"
            "  matched_rules, evaluation_duration_ms, approval_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "corrupt-1",
                "2026-03-01T12:00:00+00:00",
                "agent-1",
                "task-1",
                "shell_exec",
                "code_execution",
                "code:write",
                _DUMMY_HASH,
                "allow",
                "low",
                "test reason",
                "{BAD JSON}",
                1.5,
                None,
            ),
        )
        await migrated_db.commit()

        repo = SQLiteAuditRepository(migrated_db)
        with pytest.raises(QueryError, match="deserialize"):
            await repo.query()

    async def test_query_null_agent_id_is_unfiltered(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """agent_id=None means 'no filter', not 'filter for NULL'."""
        repo = SQLiteAuditRepository(migrated_db)
        e1 = _make_entry(entry_id="ae-with", agent_id="agent-x")
        e2 = _make_entry(entry_id="ae-without", agent_id=None)
        await repo.save(e1)
        await repo.save(e2)

        results = await repo.query(agent_id=None)
        assert len(results) == 2

    async def test_save_append_only(self, migrated_db: aiosqlite.Connection) -> None:
        """Saving same ID twice raises DuplicateRecordError (no upsert)."""
        repo = SQLiteAuditRepository(migrated_db)
        entry = _make_entry(entry_id="ae-dup")
        await repo.save(entry)

        with pytest.raises(DuplicateRecordError):
            await repo.save(entry)

    async def test_query_filter_by_until(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Entries after until are excluded."""
        repo = SQLiteAuditRepository(migrated_db)
        now = datetime.now(UTC)
        future = now + timedelta(hours=2)
        cutoff = now + timedelta(hours=1)

        e_now = _make_entry(entry_id="ae-now", timestamp=now)
        e_future = _make_entry(entry_id="ae-future", timestamp=future)
        await repo.save(e_now)
        await repo.save(e_future)

        results = await repo.query(until=cutoff)
        assert len(results) == 1
        assert results[0].id == "ae-now"

    async def test_query_since_and_until(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """since + until creates a bounded time range."""
        repo = SQLiteAuditRepository(migrated_db)
        base = datetime(2026, 3, 1, tzinfo=UTC)
        for i in range(5):
            await repo.save(
                _make_entry(
                    entry_id=f"ae-{i}",
                    timestamp=base + timedelta(hours=i),
                )
            )

        results = await repo.query(
            since=base + timedelta(hours=1),
            until=base + timedelta(hours=3),
        )
        assert {e.id for e in results} == {"ae-1", "ae-2", "ae-3"}

    async def test_query_until_before_since_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """until < since raises QueryError."""
        repo = SQLiteAuditRepository(migrated_db)
        now = datetime.now(UTC)
        with pytest.raises(QueryError, match="until"):
            await repo.query(since=now, until=now - timedelta(hours=1))

    async def test_since_boundary_inclusive(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Entry with timestamp == since is included (>= semantics)."""
        repo = SQLiteAuditRepository(migrated_db)
        cutoff = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        entry = _make_entry(entry_id="ae-boundary", timestamp=cutoff)
        await repo.save(entry)

        results = await repo.query(since=cutoff)
        assert len(results) == 1
        assert results[0].id == "ae-boundary"

    async def test_save_normalizes_timestamp_to_utc(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Non-UTC timestamps are stored as UTC for correct ordering."""
        repo = SQLiteAuditRepository(migrated_db)
        # Use timezone offset +05:30
        offset = timezone(timedelta(hours=5, minutes=30))
        ts = datetime(2026, 3, 10, 12, 0, 0, tzinfo=offset)
        entry = _make_entry(entry_id="ae-tz", timestamp=ts)
        await repo.save(entry)

        # Querying with UTC equivalent should find the entry
        utc_equiv = ts.astimezone(UTC)
        results = await repo.query(since=utc_equiv)
        assert len(results) == 1
        assert results[0].id == "ae-tz"

    async def test_save_db_error_raises_query_error(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Generic sqlite3.Error during save raises QueryError."""
        import sqlite3

        repo = SQLiteAuditRepository(migrated_db)
        entry = _make_entry(entry_id="ae-err")

        with (
            patch.object(
                migrated_db,
                "execute",
                new_callable=AsyncMock,
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            pytest.raises(QueryError, match="Failed to save"),
        ):
            await repo.save(entry)

    async def test_query_db_error_raises_query_error(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Generic sqlite3.Error during query raises QueryError."""
        import sqlite3

        repo = SQLiteAuditRepository(migrated_db)

        with (
            patch.object(
                migrated_db,
                "execute",
                new_callable=AsyncMock,
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            pytest.raises(QueryError, match="Failed to query"),
        ):
            await repo.query()

    async def test_sqlite_audit_repo_satisfies_protocol(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """SQLiteAuditRepository is an AuditRepository."""
        repo = SQLiteAuditRepository(migrated_db)
        assert isinstance(repo, AuditRepository)
