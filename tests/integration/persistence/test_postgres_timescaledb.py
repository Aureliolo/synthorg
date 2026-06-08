"""Integration tests for TimescaleDB hypertable support.

Locks the behaviour of the Postgres backend's ``enable_timescaledb``
opt-in knob: when enabled, ``cost_records`` and ``audit_entries`` are
converted to hypertables with the configured chunk interval.
``heartbeats`` stays a regular table (it is
update-heavy, not append-only, so hypertables are the wrong fit).

The repository code path must work unchanged against a hypertable-backed
table because hypertables are transparent to regular SQL.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.budget.cost_record import CostRecord
from synthorg.core.types import NotBlankStr
from synthorg.persistence.jsonb_capability import JsonbQueryCapability
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import AuditEntry
from tests._shared import sid
from tests.unit.persistence.conftest import make_task

pytestmark = [
    pytest.mark.integration,
    # Session-scoped TimescaleDB container startup can exceed the default
    # 30s timeout on slow CI runners; the test body itself is fast.
    pytest.mark.timeout(120),
]


async def _fetchone(
    backend: PostgresPersistenceBackend,
    sql: str,
    params: tuple[object, ...] | None = None,
) -> tuple[object, ...] | None:
    pool = backend._pool
    assert pool is not None
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(cast(Any, sql), params)
        return await cur.fetchone()


@pytest.mark.timescaledb
class TestHypertableConversion:
    async def test_cost_records_is_hypertable(
        self,
        timescaledb_backend: PostgresPersistenceBackend,
    ) -> None:
        row = await _fetchone(
            timescaledb_backend,
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'cost_records'",
        )
        assert row is not None
        assert row[0] == "cost_records"

    async def test_audit_entries_is_hypertable(
        self,
        timescaledb_backend: PostgresPersistenceBackend,
    ) -> None:
        row = await _fetchone(
            timescaledb_backend,
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'audit_entries'",
        )
        assert row is not None
        assert row[0] == "audit_entries"

    async def test_heartbeats_is_not_hypertable(
        self,
        timescaledb_backend: PostgresPersistenceBackend,
    ) -> None:
        row = await _fetchone(
            timescaledb_backend,
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'heartbeats'",
        )
        assert row is None, "heartbeats must stay a regular table -- it is update-heavy"


@pytest.mark.integration
class TestVanillaPostgresFallback:
    """Vanilla Postgres (no TimescaleDB) still runs the same schema."""

    async def test_disabled_falls_back_to_plain_table(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """With enable_timescaledb=False cost_records is a plain table.

        Uses the vanilla ``postgres_backend`` fixture (Postgres 18
        Alpine, no TimescaleDB binary) to confirm the opt-out path
        is clean: the schema migrates successfully and the tables
        are not hypertables (because there IS no TimescaleDB here at
        all).
        """
        pool = postgres_backend._pool
        assert pool is not None
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'",
            )
            row = await cur.fetchone()
            assert row is None, (
                "Unexpected TimescaleDB in vanilla postgres_backend fixture"
            )
            await cur.execute(
                "SELECT COUNT(*) FROM pg_class WHERE relname = 'cost_records'",
            )
            count_row = await cur.fetchone()
            assert count_row is not None
            assert count_row[0] == 1


@pytest.mark.timescaledb
class TestRepositoryTransparency:
    """Repository code is unchanged under hypertables -- same behaviour."""

    async def test_cost_records_save_and_query(
        self,
        timescaledb_backend: PostgresPersistenceBackend,
    ) -> None:
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        # cost_records has an FK to tasks; seed the referenced tasks first.
        for task_idx in range(3):
            await timescaledb_backend.tasks.save(
                make_task(task_id=f"task-{task_idx}"),
            )

        for i in range(50):
            # Stagger timestamps across ~7 days so the hypertable
            # actually partitions the data into multiple chunks.
            record = CostRecord(
                agent_id=NotBlankStr(f"agent-{i % 5}"),
                task_id=NotBlankStr(sid(f"task-{i % 3}")),
                provider=NotBlankStr("test-provider"),
                model=NotBlankStr("test-small-001"),
                input_tokens=100,
                output_tokens=50,
                cost=0.001,
                currency="EUR",
                timestamp=base + timedelta(days=i % 7, hours=i),
                call_category=None,
            )
            await timescaledb_backend.cost_records.append(record)

        from synthorg.persistence.cost_record_protocol import (
            CostRecordFilterSpec,
        )

        results = await timescaledb_backend.cost_records.query(CostRecordFilterSpec())
        assert len(results) == 50

        # Hypertable partitioning check: multiple chunks exist.
        chunk_row = await _fetchone(
            timescaledb_backend,
            "SELECT num_chunks FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'cost_records'",
        )
        assert chunk_row is not None
        num_chunks = chunk_row[0]
        assert isinstance(num_chunks, int)
        assert num_chunks >= 2, (
            f"expected >=2 chunks after 7-day spread, got {num_chunks}"
        )

    async def test_audit_repo_jsonb_query_still_works(
        self,
        timescaledb_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = timescaledb_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        now = datetime.now(UTC)
        for i in range(5):
            await repo.append(
                AuditEntry(
                    id=NotBlankStr(f"audit-{i}"),
                    timestamp=now,
                    agent_id=NotBlankStr("agent-1"),
                    task_id=NotBlankStr("task-1"),
                    tool_name=NotBlankStr("test-tool"),
                    tool_category=ToolCategory.TERMINAL,
                    action_type=NotBlankStr("execute"),
                    arguments_hash=NotBlankStr("0" * 64),
                    verdict="allow",
                    risk_level=ApprovalRiskLevel.LOW,
                    reason=NotBlankStr("bench"),
                    matched_rules=(NotBlankStr("rule-target"),) if i % 2 == 0 else (),
                    evaluation_duration_ms=1.0,
                    approval_id=None,
                ),
            )

        entries, total = await repo.query_jsonb_contains(
            "matched_rules",
            ["rule-target"],
            limit=10,
        )
        assert total == 3
        assert len(entries) == 3
